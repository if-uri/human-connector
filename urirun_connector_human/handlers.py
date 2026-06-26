"""Handlers for the `human://` connector — the reusable logic, with no urirun
import and no web framework. Each handler is `fn(payload, store, memory) -> envelope`.

URI surface (authority `{node}` is the physical cell, e.g. `cell-a`):

    human://{node}/task/command/request           post a human task -> pending
    human://{node}/task/query/poll                check a task -> pending|done|declined
    human://{node}/task/command/resolve           worker side: submit the result
    human://{node}/task/command/cancel            cancel (used by reversibility)
    human://{node}/precondition/command/satisfy   Faza 6 provider: acquire a per-env grant

Two envelope shapes carry "a human still has to act":

  * a TASK STEP that is pending returns  next:{kind:"await", on:"human", ...}
    -> the flow driver suspends this step and resumes when the task resolves.
  * a PRECONDITION that needs a human returns next:{kind:"acquire", provider:..., ...}
    -> the ensure-loop calls the provider's `satisfy` route, then retries.

`next.kind` matches what `urirun.node.flow_thin._next_kind` already reads.
"""
from __future__ import annotations

import os
import time
from typing import Optional

from ._envelope import fail, ok, verification
from .episode import environment_fingerprint
from .store import TaskStore

# Where the human surface lives, so envelopes can hand the host a tap-able link.
SURFACE_URL = os.environ.get("URIRUN_HUMAN_SURFACE", "http://localhost:8788")


def _node_of(payload: dict, default: str = "cell-a") -> str:
    return payload.get("node") or payload.get("env") or default


def _surface_links(node: str, task_id: str) -> dict:
    return {
        "url": SURFACE_URL,
        "taskUrl": f"{SURFACE_URL}/t/{task_id}",
        "queueUrl": f"{SURFACE_URL}/?node={node}",
    }


# --------------------------------------------------------------------------- #
# human://{node}/task/command/request
# --------------------------------------------------------------------------- #
def request_task(payload: dict, store: TaskStore, memory) -> dict:
    """Post a unit of human work and return immediately as *pending*.

    The connector never blocks on a person. It records the task, emits a
    `human.task.requested` event, and returns a `next:await` envelope so the
    flow driver can suspend this step and resume on resolution.
    """
    node = _node_of(payload)
    title = payload.get("title")
    if not title:
        return fail("ValueError", "request requires a 'title'",
                    uri=f"human://{node}/task/command/request")

    scope = payload.get("scope", "per-instance")
    task = store.create_task(
        node=node,
        title=title,
        instruction=payload.get("instruction", ""),
        env=payload.get("env", node),
        scope=scope,
        kind=payload.get("kind", "action"),
        payload=payload,
        inverse=payload.get("inverse"),  # how to undo it, if the worker confirms
    )
    tid = task["id"]
    return ok(
        status="pending",
        task={"id": tid, "title": title, "scope": scope,
              "kind": task["kind"], "env": task["env"]},
        surface=_surface_links(node, tid),
        next={
            "kind": "await",
            "on": "human",
            "poll": f"human://{node}/task/query/poll",
            "payload": {"taskId": tid},
            "resume": True,
        },
        verification=verification("human-task.v1", False,
                                  expected="human-confirmation", actual="pending"),
    )


# --------------------------------------------------------------------------- #
# human://{node}/task/query/poll
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
        return ok(
            status="pending",
            task={"id": tid, "status": status, "title": task["title"]},
            next={"kind": "await", "on": "human",
                  "poll": f"human://{node}/task/query/poll", "payload": {"taskId": tid}},
        )

    if status == "done":
        artifact = None
        if task.get("proofPath"):
            artifact = {
                "kind": "human-proof-photo",
                "path": task["proofPath"],
                "mime": "image/jpeg",
                "live": False,
                "meta": {"taskId": tid, "by": task["resolvedBy"]},
            }
        return ok(
            status="done",
            task={"id": tid, "title": task["title"], "scope": task["scope"]},
            result={"outcome": "done", "by": task["resolvedBy"],
                    **(task["result"] or {})},
            inverse=task.get("inverse"),  # reversibility: replayed on rollback
            artifact=artifact,
            verification=verification("human-task.v1", True,
                                      expected="human-confirmation", actual="confirmed",
                                      by=task["resolvedBy"]),
        )

    if status == "declined":
        return fail("HumanDeclined",
                    (task.get("result") or {}).get("note", "worker declined the task"),
                    uri=f"human://{node}/task/command/resolve",
                    status="declined",
                    result={"outcome": "declined", "by": task["resolvedBy"],
                            **(task["result"] or {})})

    # cancelled
    return fail("Cancelled", "task was cancelled", status="cancelled")


# --------------------------------------------------------------------------- #
# human://{node}/task/command/resolve   (worker side — called by the surface)
# --------------------------------------------------------------------------- #
def resolve_task(payload: dict, store: TaskStore, memory) -> dict:
    """A person submits the outcome of a task. This is the human's edge of the
    bridge — the surface POSTs here, or a robot's human-operator confirms here.
    """
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
    updated = store.resolve(
        tid, by=by, outcome=outcome,
        note=payload.get("note", ""),
        result=payload.get("result"),
        proof_path=payload.get("proofPath"),
    )
    return ok(status=updated["status"], task={"id": tid},
              result={"outcome": outcome, "by": by})


# --------------------------------------------------------------------------- #
# human://{node}/task/command/cancel    (reversibility / cleanup)
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
# human://{node}/precondition/command/satisfy   (Faza 6 provider)
# --------------------------------------------------------------------------- #
def satisfy_precondition(payload: dict, store: TaskStore, memory) -> dict:
    """Acquire a per-environment human grant (operator authorisation, a login,
    a calibration sign-off...). This is the connector acting as a *provider* for
    a `next:{kind:"acquire"}` need.

    The honest reuse rule lives here:
      - if the env already has a remembered proof and the fingerprint matches
        (no drift) -> SATISFIED, recalled, the human is NOT asked again.
      - otherwise -> post a human task and return pending; once the human grants,
        the proof is remembered for this exact env fingerprint.

    `scope: per-instance` needs are refused here — they belong on the task route,
    because caching a per-instance physical action would be unsafe.
    """
    node = _node_of(payload)
    need = payload.get("need") or {}
    what = need.get("what") or payload.get("what")
    if not what:
        return fail("ValueError", "satisfy requires need.what",
                    uri=f"human://{node}/precondition/command/satisfy")

    if need.get("scope", "per-env") == "per-instance":
        return fail("Unsupported",
                    "per-instance needs must use task/command/request, not satisfy",
                    uri=f"human://{node}/precondition/command/satisfy")

    env_profile = payload.get("envProfile") or {"env": node}
    env_fp = environment_fingerprint(env_profile)
    drift = memory.drift(what, env_fp)

    # 1) Already satisfied for this exact environment -> recall, skip the human.
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

    # 2) A pending grant task already exists for this need+env? resume it.
    pending_tid = payload.get("taskId")
    task = store.get_task(pending_tid) if pending_tid else None
    if task and task["status"] == "done":
        proof = {"what": what, "envFp": env_fp,
                 "grantedBy": task["resolvedBy"], "at": task["updated"],
                 **(task["result"] or {})}
        memory.remember_proof(what, env_fp, proof)   # asked once; remembered per env
        return ok(
            status="satisfied",
            recalled=False,
            need=need,
            proof={**proof, "recalled": False},
            verification=verification("precondition.v1", True,
                                      expected=what, actual="granted",
                                      by=task["resolvedBy"]),
        )

    # 3) Nothing yet -> post the grant task, return acquire/pending.
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
            "provider": f"human://{node}/precondition/command/satisfy",
            "poll": f"human://{node}/task/query/poll",
            "payload": {"taskId": tid, "need": need, "envProfile": env_profile, "node": node},
        },
        verification=verification("precondition.v1", False,
                                  expected=what, actual="pending"),
    )
