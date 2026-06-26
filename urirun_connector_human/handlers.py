"""Handlers for the `human://` connector — the reusable logic, with no urirun
import and no web framework. Each handler is `fn(payload, store, memory) -> envelope`.

URI surface (canonical urirun routes, (resource,operation) pairs):

    human://{node}/task/create     — post a human task -> pending + next:await
    human://{node}/task/poll       — check a task -> pending|done|declined
    human://{node}/task/resolve    — worker side: submit the result
    human://{node}/task/cancel     — cancel (used by reversibility)
    human://{node}/task/claim      — reserve a task for a specific worker
    human://{node}/grant/satisfy   — Phase 6 provider: acquire a per-env grant

Task kinds:
    action      — physical step, Done/Decline buttons
    safety      — safety check, same buttons but styled as warning
    judgement   — qualitative decision, Done/Decline
    grant       — per-env operator authorisation, recalled by TwinMemory
    choice      — multiple option buttons (payload.options: list[str])
    form        — structured fields (payload.fields: list[{name,type,label,required}])

Two envelope shapes carry "a human still has to act":
  * TASK STEP pending  → next:{kind:"await", on:"human", ...}
  * PRECONDITION need  → next:{kind:"acquire", provider:..., ...}
"""
from __future__ import annotations

import os
import time
from typing import Optional

from ._envelope import fail, ok, verification
from .episode import environment_fingerprint
from .store import TaskStore

SURFACE_URL = os.environ.get("URIRUN_HUMAN_SURFACE", "http://localhost:8788")


def _node_of(payload: dict, default: str = "cell-a") -> str:
    return payload.get("node") or payload.get("env") or default


def _surface_links(node: str, task_id: str) -> dict:
    return {
        "url": SURFACE_URL,
        "taskUrl": f"{SURFACE_URL}/t/{task_id}",
        "queueUrl": f"{SURFACE_URL}/?node={node}",
    }


def _infer_kind(payload: dict) -> str:
    """Infer task kind from payload — explicit > structural > default."""
    if payload.get("kind"):
        return payload["kind"]
    if payload.get("options"):
        return "choice"
    if payload.get("fields"):
        return "form"
    return "action"


# --------------------------------------------------------------------------- #
# human://{node}/task/create
# --------------------------------------------------------------------------- #
def request_task(payload: dict, store: TaskStore, memory) -> dict:
    """Post a unit of human work and return immediately as *pending*.

    Supports three interaction modes via the task kind:
      - action/safety/judgement/grant  → Done / Decline buttons
      - choice   → one button per option in payload.options
      - form     → structured form fields from payload.fields

    Optional: payload.deadline (unix timestamp) — surface shows countdown,
    flow driver can use it to escalate.
    """
    node = _node_of(payload)
    title = payload.get("title")
    if not title:
        return fail("ValueError", "request requires a 'title'",
                    uri=f"human://{node}/task/create")

    kind = _infer_kind(payload)
    options = payload.get("options")  # list[str] for kind=choice
    fields = payload.get("fields")    # list[{name,type,label,required}] for kind=form
    deadline = payload.get("deadline")

    # Validate choice options
    if kind == "choice" and (not options or not isinstance(options, list)):
        return fail("ValueError", "kind=choice requires a non-empty 'options' list",
                    uri=f"human://{node}/task/create")

    scope = payload.get("scope", "per-instance")
    task = store.create_task(
        node=node,
        title=title,
        instruction=payload.get("instruction", ""),
        env=payload.get("env", node),
        scope=scope,
        kind=kind,
        payload=payload,
        inverse=payload.get("inverse"),
        options=options,
        fields=fields,
        deadline=deadline,
    )
    tid = task["id"]
    return ok(
        status="pending",
        task={"id": tid, "title": title, "scope": scope,
              "kind": kind, "env": task["env"]},
        surface=_surface_links(node, tid),
        next={
            "kind": "await",
            "on": "human",
            "poll": f"human://{node}/task/poll",
            "payload": {"taskId": tid},
            "resume": True,
        },
        verification=verification("human-task.v1", False,
                                  expected="human-confirmation", actual="pending"),
    )


# --------------------------------------------------------------------------- #
# human://{node}/task/poll
# --------------------------------------------------------------------------- #
def poll_task(payload: dict, store: TaskStore, memory) -> dict:
    """Read a task's current state. Terminal states carry the proof + inverse."""
    tid = payload.get("taskId") or payload.get("id")
    if not tid:
        return fail("ValueError", "poll requires 'taskId'")
    task = store.get_task(tid)
    if not task:
        return fail("NotFound", f"no task {tid}")

    node = task["node"]
    status = task["status"]

    if status in ("open", "claimed"):
        # Check deadline expiry — surface shows countdown, here we surface the warning
        deadline = task.get("deadline")
        overdue = deadline and time.time() > deadline
        return ok(
            status="pending",
            task={"id": tid, "status": status, "title": task["title"],
                  "overdue": overdue},
            next={"kind": "await", "on": "human",
                  "poll": f"human://{node}/task/poll", "payload": {"taskId": tid}},
        )

    if status == "done":
        result = task.get("result") or {}
        artifact = None
        if task.get("proofPath"):
            artifact = {
                "kind": "human-proof-photo",
                "path": task["proofPath"],
                "mime": "image/jpeg",
                "live": False,
                "meta": {"taskId": tid, "by": task["resolvedBy"]},
            }
        proof = {
            "outcome": "done",
            "by": task["resolvedBy"],
            "choice": result.get("choice"),     # for kind=choice
            "formData": result.get("formData"), # for kind=form
            "note": result.get("note"),
        }
        return ok(
            status="done",
            task={"id": tid, "title": task["title"], "scope": task["scope"],
                  "kind": task["kind"]},
            result={k: v for k, v in proof.items() if v is not None},
            inverse=task.get("inverse"),
            artifact=artifact,
            proof={k: v for k, v in proof.items() if v is not None},
            verification=verification("human-task.v1", True,
                                      expected="human-confirmation", actual="confirmed",
                                      by=task["resolvedBy"]),
        )

    if status == "declined":
        result = task.get("result") or {}
        return fail("HumanDeclined",
                    result.get("note", "worker declined the task"),
                    uri=f"human://{node}/task/resolve",
                    status="declined",
                    result={"outcome": "declined", "by": task["resolvedBy"],
                            **{k: v for k, v in result.items() if k != "note"}})

    return fail("Cancelled", "task was cancelled", status="cancelled")


# --------------------------------------------------------------------------- #
# human://{node}/task/resolve
# --------------------------------------------------------------------------- #
def resolve_task(payload: dict, store: TaskStore, memory) -> dict:
    """A person submits the outcome of a task."""
    tid = payload.get("taskId") or payload.get("id")
    if not tid:
        return fail("ValueError", "resolve requires 'taskId'")
    task = store.get_task(tid)
    if not task:
        return fail("NotFound", f"no task {tid}")
    if task["status"] in ("done", "declined", "cancelled"):
        return fail("AlreadyResolved", f"task {tid} is already {task['status']}")

    outcome = payload.get("outcome", "done")
    by = payload.get("by", "worker")

    # Validate choice outcome against allowed options
    kind = task.get("kind")
    options = task.get("options")
    if kind == "choice" and options:
        choice = payload.get("choice") or outcome
        if choice not in options and outcome not in ("done", "declined"):
            return fail("InvalidChoice",
                        f"'{choice}' is not in options: {options}")
        # normalise: outcome=done, choice=selected option
        outcome = "done"
        payload = {**payload, "choice": choice}

    # For form tasks: validate required fields
    fields_def = task.get("fields") or []
    form_data = payload.get("formData") or {}
    if kind == "form" and fields_def:
        missing = [f["name"] for f in fields_def
                   if f.get("required") and not form_data.get(f["name"])]
        if missing:
            return fail("MissingFields",
                        f"required form fields missing: {missing}")

    result = {}
    if payload.get("choice"):
        result["choice"] = payload["choice"]
    if form_data:
        result["formData"] = form_data
    if payload.get("note"):
        result["note"] = payload["note"]

    updated = store.resolve(
        tid, by=by, outcome=outcome,
        note=payload.get("note", ""),
        result=result or None,
        proof_path=payload.get("proofPath"),
    )
    return ok(status=updated["status"], task={"id": tid, "kind": kind},
              result={"outcome": outcome, "by": by, **result})


# --------------------------------------------------------------------------- #
# human://{node}/task/claim
# --------------------------------------------------------------------------- #
def claim_task(payload: dict, store: TaskStore, memory) -> dict:
    """Reserve a task for a specific worker (prevents double-take in multi-worker queues)."""
    tid = payload.get("taskId") or payload.get("id")
    if not tid:
        return fail("ValueError", "claim requires 'taskId'")
    by = payload.get("by", "worker")
    task = store.claim(tid, by)
    if not task:
        return fail("NotFound", f"no task {tid}")
    if task["status"] != "claimed":
        return fail("AlreadyClaimed",
                    f"task {tid} is {task['status']} (claimed by {task.get('claimedBy')})")
    return ok(status="claimed", task={"id": tid, "claimedBy": by})


# --------------------------------------------------------------------------- #
# human://{node}/task/cancel
# --------------------------------------------------------------------------- #
def cancel_task(payload: dict, store: TaskStore, memory) -> dict:
    tid = payload.get("taskId") or payload.get("id")
    if not tid:
        return fail("ValueError", "cancel requires 'taskId'")
    task = store.cancel(tid, reason=payload.get("reason", ""))
    if not task:
        return fail("NotFound", f"no task {tid}")
    return ok(status="cancelled", task={"id": tid})


# --------------------------------------------------------------------------- #
# human://{node}/grant/satisfy   (Phase 6 provider)
# --------------------------------------------------------------------------- #
def satisfy_precondition(payload: dict, store: TaskStore, memory) -> dict:
    """Acquire a per-environment human grant.

    Honest reuse rule:
      - env fingerprint matches known-good  → SATISFIED, recalled (human skipped)
      - pending grant task already exists   → resume (return same next:acquire)
      - grant task just completed           → remember proof, return satisfied
      - nothing yet                         → create task, return acquire/pending
    """
    node = _node_of(payload)
    need = payload.get("need") or {}
    what = need.get("what") or payload.get("what")
    if not what:
        return fail("ValueError", "satisfy requires need.what",
                    uri=f"human://{node}/grant/satisfy")

    if need.get("scope", "per-env") == "per-instance":
        return fail("Unsupported",
                    "per-instance needs must use task/create, not grant/satisfy",
                    uri=f"human://{node}/grant/satisfy")

    env_profile = payload.get("envProfile") or {"env": node}
    env_fp = environment_fingerprint(env_profile)
    drift = memory.drift(what, env_fp)

    if drift == "matches-known-good":
        proof = memory.recall_proof(what, env_fp)
        return ok(
            status="satisfied",
            recalled=True,
            need=need,
            proof={**proof, "recalled": True, "envFp": env_fp},
            verification=verification("precondition.v1", True,
                                      expected=what, actual="recalled", recalled=True),
        )

    pending_tid = payload.get("taskId")
    task = store.get_task(pending_tid) if pending_tid else None
    if task and task["status"] == "done":
        proof = {"what": what, "envFp": env_fp,
                 "grantedBy": task["resolvedBy"], "at": task["updated"],
                 **(task["result"] or {})}
        memory.remember_proof(what, env_fp, proof)
        return ok(
            status="satisfied",
            recalled=False,
            need=need,
            proof={**proof, "recalled": False},
            verification=verification("precondition.v1", True,
                                      expected=what, actual="granted",
                                      by=task["resolvedBy"]),
        )

    if not task:
        task = store.create_task(
            node=node,
            title=payload.get("title") or f"Authorise: {what}",
            instruction=payload.get("instruction")
            or f"Per-environment grant for '{what}' on {node}. "
               "Confirm once for this shift/environment.",
            env=node,
            scope="per-env",
            kind="grant",
            payload={"need": need, "envFp": env_fp},
        )
    tid = task["id"]
    return ok(
        status="pending",
        need=need,
        task={"id": tid, "scope": "per-env", "what": what, "env": node},
        surface=_surface_links(node, tid),
        next={
            "kind": "acquire",
            "need": need,
            "by": "human",
            "provider": f"human://{node}/grant/satisfy",
            "poll": f"human://{node}/task/poll",
            "payload": {"taskId": tid, "need": need,
                        "envProfile": env_profile, "node": node},
        },
        verification=verification("precondition.v1", False,
                                  expected=what, actual="pending"),
    )
