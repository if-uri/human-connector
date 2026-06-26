"""_shim.py — a tiny stand-in for urirun's runtime, so the example runs with no
dependencies. Everything here maps to something real in the tree:

  dispatch()      -> urirun.host.dispatch.make_local_dispatch_uri / make_dispatch_uri
  run_flow()      -> urirun.node.flow_thin._thin_driver  (honours result["next"]["kind"])
  ledger/rollback -> urirun.node.reversible.ReversibleProcess.rollback_flow
  capture episode -> urirun.node.twin_bridge.capture_episode + TwinMemory.remember_*
  EventBus        -> the node EventHub / _publish_step_event SSE stream

The mock `inventory://`, `robot://` and `dispatch://` connectors stand in for
OTHER nodes/connectors (an inventory API node, a robot arm node, an orders
service). Only `human://` is the real deliverable; it is dispatched through the
package's own handlers.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from urirun_connector_human import connector  # the real human:// handlers
from urirun_connector_human.episode import environment_fingerprint, intent_signature

PROOF_DIR = Path.home() / ".urirun-human" / "proofs"
PROOF_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Mock connectors for the non-human schemes (machine + robot + orders service)
# --------------------------------------------------------------------------- #
def _inventory(uri: str, payload: dict) -> dict:
    return {"ok": True, "connector": "inventory", "sku": payload.get("sku"),
            "bin": "C-3", "available": True,
            "verification": {"contract": "stock.v1", "ok": True,
                             "expected": "located", "actual": "bin C-3"}}


def _robot(uri: str, payload: dict) -> dict:
    # A real robot node would actuate hardware here. It returns an INVERSE so the
    # reversible ledger can move the item back if a later step fails.
    frm, to = payload.get("from"), payload.get("to")
    return {
        "ok": True, "connector": "robot", "moved": True, "via": "ur5e-sim",
        "from": frm, "to": to,
        "inverse": {"uri": uri, "payload": {**payload, "from": to, "to": frm}},
        "verification": {"contract": "robot-move.v1", "ok": True,
                         "expected": f"{frm}->{to}", "actual": "moved"},
    }


_FAIL_ORDERS = {"enabled": False}


def _dispatch(uri: str, payload: dict) -> dict:
    if _FAIL_ORDERS["enabled"]:
        return {"ok": False, "connector": "dispatch",
                "error": {"type": "CarrierError",
                          "message": "carrier API rejected the manifest", "uri": uri}}
    return {"ok": True, "connector": "dispatch", "shipped": True,
            "order": payload.get("order"),
            "verification": {"contract": "shipment.v1", "ok": True,
                             "expected": "shipped", "actual": "label printed"}}


def dispatch(uri: str, payload: dict) -> dict:
    """Route a concrete URI to its connector. human:// -> real handlers; others -> mocks."""
    scheme = uri.split("://", 1)[0]
    if scheme == "human":
        return connector.local_dispatch(uri, payload)
    if scheme == "inventory":
        return _inventory(uri, payload)
    if scheme == "robot":
        return _robot(uri, payload)
    if scheme == "dispatch":
        return _dispatch(uri, payload)
    return {"ok": False, "error": {"type": "RouteNotFound", "message": uri, "uri": uri}}


# --------------------------------------------------------------------------- #
# Event stream (prints machine/robot/system + drains human task events)
# --------------------------------------------------------------------------- #
SCHEME_ICON = {"human": "🧍", "robot": "🤖", "inventory": "📦", "dispatch": "🚚"}


class EventBus:
    def __init__(self, store) -> None:
        self.store = store
        self._seq = max((e["seq"] for e in store.events_since(0)), default=0)

    def drain(self) -> None:
        for ev in self.store.events_since(self._seq):
            self._seq = ev["seq"]
            d = ev["data"]
            extra = d.get("title") or d.get("outcome") or d.get("by") or ""
            print(f"      · stream  {ev['type']:<22} {extra}")

    def step(self, sid: str, uri: str, result: dict) -> None:
        self.drain()
        icon = SCHEME_ICON.get(uri.split('://', 1)[0], "•")
        status = result.get("status") or ("ok" if result.get("ok") else "fail")
        tail = ""
        if result.get("recalled"):
            tail = "  ⟲ RECALLED (human not asked)"
        elif result.get("connector") == "robot":
            tail = f"  {result.get('from')}→{result.get('to')}"
        elif result.get("connector") == "inventory":
            tail = f"  bin {result.get('bin')}"
        print(f"  {icon} {sid:<16} {uri:<46} [{status}]{tail}")

    def note(self, text: str) -> None:
        self.drain()
        print(f"      {text}")


# --------------------------------------------------------------------------- #
# Auto-worker: simulates people tapping the surface (headless demo)
# --------------------------------------------------------------------------- #
def start_auto_worker(store, *, delay: float = 0.4, stop: threading.Event | None = None):
    stop = stop or threading.Event()

    def loop():
        while not stop.is_set():
            for t in store.list_open():
                time.sleep(delay)  # a person taking a moment
                proof = None
                if t["kind"] == "action":
                    proof = str(PROOF_DIR / f"{t['id']}.jpg")
                    Path(proof).write_bytes(b"\xff\xd8\xff\xe0demo-photo")  # tiny stub jpg
                store.resolve(t["id"], by="warehouse-worker-1", outcome="done",
                              note="confirmed on the floor", proof_path=proof)
            stop.wait(0.1)

    th = threading.Thread(target=loop, daemon=True)
    th.start()
    return stop


def await_resolution(store, task_id: str, timeout: float = 20.0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = store.get_task(task_id)
        if t and t["status"] in ("done", "declined", "cancelled"):
            return t
        time.sleep(0.1)
    return None


# --------------------------------------------------------------------------- #
# The next-aware driver (the heart that treats human steps like any other)
# --------------------------------------------------------------------------- #
def _ensure_satisfied(provider: str, ppayload: dict, bus: EventBus, store) -> dict:
    res = dispatch(provider, ppayload)
    guard = 0
    while res.get("ok") and res.get("status") == "pending" and guard < 12:
        guard += 1
        tid = (res.get("task") or {}).get("id") or ppayload.get("taskId")
        bus.note(f"↳ awaiting per-env grant task {tid}")
        await_resolution(store, tid)
        bus.drain()
        res = dispatch(provider, {**ppayload, "taskId": tid})
    return res


def _advance(result: dict, uri: str, payload: dict, bus: EventBus, store) -> dict:
    """Resolve a step's `next` directives until it is terminally ok or failed."""
    guard = 0
    while guard < 12:
        guard += 1
        nx = result.get("next") or {}
        kind = nx.get("kind")
        if not result.get("ok") or not kind:
            return result

        if kind == "acquire":
            bus.note(f"↳ precondition acquire: {nx.get('need', {}).get('what')} "
                     f"via {nx.get('provider')}")
            satisfied = _ensure_satisfied(nx["provider"], dict(nx["payload"]), bus, store)
            if not (satisfied.get("ok") and satisfied.get("status") == "satisfied"):
                return satisfied
            if uri == nx["provider"]:
                result = satisfied
            else:
                bus.note(f"↳ retrying {uri} after acquire")
                result = dispatch(uri, payload)
            continue

        if kind == "await":
            tid = nx["payload"]["taskId"]
            bus.note(f"↳ awaiting human task {tid}")
            await_resolution(store, tid)
            bus.drain()
            result = dispatch(nx["poll"], dict(nx["payload"]))
            continue

        return result
    return result


def rollback(ledger: list[tuple[str, dict]], bus: EventBus, store) -> None:
    if not ledger:
        return
    print("\n  ⟲ ROLLBACK (replaying inverses, newest first):")
    for uri, inv in reversed(ledger):
        inv_uri, inv_payload = inv["uri"], inv.get("payload", {})
        res = dispatch(inv_uri, dict(inv_payload))
        res = _advance(res, inv_uri, inv_payload, bus, store)  # human inverses suspend too
        ok = res.get("ok")
        print(f"     ↩ {inv_uri:<46} [{'ok' if ok else 'fail'}]  (undoes {uri.split('://')[0]})")


def run_flow(flow: dict, bus: EventBus, memory, store, *, fail_orders: bool = False) -> dict:
    intent = flow["intent"]
    env = flow["environment"]
    isig, efp = intent_signature(intent), environment_fingerprint(env)
    _FAIL_ORDERS["enabled"] = fail_orders

    print(f"\n▶ flow: {flow['task']['id']}   intent={isig}  env={efp}")
    known = memory.known_good_episode(isig, efp)
    print(f"  recall: {'known-good episode present (reuse path)' if known else 'no known-good — first run'}")

    ledger: list[tuple[str, dict]] = []
    timeline, results, proofs = [], {}, []

    for step in flow["steps"]:
        sid, uri = step["id"], step["uri"]
        payload = dict(step.get("payload", {}))
        result = dispatch(uri, payload)
        result = _advance(result, uri, payload, bus, store)
        bus.step(sid, uri, result)
        timeline.append({"id": sid, "uri": uri,
                         "status": result.get("status") or ("ok" if result.get("ok") else "fail")})

        if not result.get("ok"):
            bus.note(f"✗ step {sid} failed: {result.get('error', {}).get('message')}")
            rollback(ledger, bus, store)
            return {"ok": False, "status": "failed", "failedAt": sid,
                    "timeline": timeline, "results": results}

        results[sid] = result
        if result.get("inverse"):
            ledger.append((uri, result["inverse"]))
        if result.get("proof") or result.get("verification", {}).get("ok"):
            proofs.append({"step": sid, "uri": uri,
                           "recalled": result.get("recalled", False),
                           "verification": result.get("verification")})

    episode = {
        "experienceId": f"exp_{flow['task']['id']}",
        "intentSig": isig, "envFp": efp,
        "plan": [s["uri"] for s in flow["steps"]],
        "proofs": proofs, "outcome": "done",
    }
    memory.remember_episode(isig, efp, episode)
    print(f"  ✓ done — episode captured ({len(proofs)} proofs, "
          f"{sum(1 for p in proofs if p['recalled'])} recalled)")
    return {"ok": True, "status": "done", "timeline": timeline,
            "results": results, "episode": episode}
