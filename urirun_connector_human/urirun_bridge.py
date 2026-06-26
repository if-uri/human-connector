"""urirun entry-point bridge — adapts human:// handlers to urirun's calling convention.

urirun calls local-function handlers as `fn(**payload_kwargs)` (via _handler_kwargs).
Each function here is a thin wrapper that converts **kwargs → dict and delegates to
the real handler. All state lives in file-backed STORE/MEMORY so the surface (a
separate process on port 8797) shares state with the node automatically.

Entry point: urirun.bindings group → `human = urirun_connector_human.urirun_bridge:urirun_bindings`
"""
from __future__ import annotations

from .connector import MEMORY, STORE
from . import handlers
from .handlers import (
    request_task as _request_task,
    poll_task as _poll_task,
    resolve_task as _resolve_task,
    cancel_task as _cancel_task,
    claim_task as _claim_task,
    satisfy_precondition as _satisfy_precondition,
)

VERSION = "urirun.bindings.v2"

# --------------------------------------------------------------------------- #
# Handler wrappers — accept **payload so _handler_kwargs passes everything through
# --------------------------------------------------------------------------- #

def request_task(**payload) -> dict:
    return _request_task(payload, STORE, MEMORY)


def poll_task(**payload) -> dict:
    return _poll_task(payload, STORE, MEMORY)


def resolve_task(**payload) -> dict:
    return _resolve_task(payload, STORE, MEMORY)


def cancel_task(**payload) -> dict:
    return _cancel_task(payload, STORE, MEMORY)


def claim_task(**payload) -> dict:
    return _claim_task(payload, STORE, MEMORY)


def satisfy_precondition(**payload) -> dict:
    return _satisfy_precondition(payload, STORE, MEMORY)


# --------------------------------------------------------------------------- #
# Bindings document — returned by the urirun.bindings entry point
# --------------------------------------------------------------------------- #

_MOD = __name__  # urirun_connector_human.urirun_bridge

_SCHEMA_NODE = {"type": "string", "description": "Physical cell, e.g. cell-a"}
_SCHEMA_SCOPE = {"type": "string", "enum": ["per-instance", "per-env"], "default": "per-instance"}
_SCHEMA_KIND = {"type": "string", "enum": ["action", "judgement", "safety", "grant"], "default": "action"}


def urirun_bindings() -> dict:
    """Called by the urirun.bindings entry point loader at node startup.

    URI layout — urirun routes by (resource, operation) so each handler must have a
    unique pair.  The canonical scheme is human://{node}/{resource}/{operation}:

      task/create     → POST a new task (returns pending + next:await)
      task/poll       → GET task status
      task/resolve    → PUT worker outcome (done/decline)
      task/cancel     → DELETE / cancel for reversibility
      grant/satisfy   → Phase-6 provider: per-env precondition grant
    """
    return {
        "version": VERSION,
        "bindings": {
            # ── create ──────────────────────────────────────────────────────────
            "human://{node}/task/create": {
                "uri": "human://{node}/task/create",
                "kind": "command",
                "adapter": "local-function",
                "python": {"type": "python", "module": _MOD, "export": "request_task"},
                "policy": {"allowExecute": True},
                "meta": {"connector": "human", "label": "Post a human task (→ pending + next:await)"},
                "inputSchema": {
                    "type": "object",
                    "required": ["title"],
                    "additionalProperties": True,
                    "properties": {
                        "node": _SCHEMA_NODE,
                        "title": {"type": "string"},
                        "instruction": {"type": "string"},
                        "scope": _SCHEMA_SCOPE,
                        "kind": {"type": "string",
                                 "enum": ["action", "judgement", "safety", "grant", "choice", "form"],
                                 "default": "action"},
                        "env": {"type": "string"},
                        "inverse": {"type": "object"},
                        "options": {"type": "array", "items": {"type": "string"},
                                    "description": "For kind=choice: list of option labels"},
                        "fields": {"type": "array",
                                   "description": "For kind=form: list of {name,type,label,required,hint}",
                                   "items": {"type": "object",
                                             "properties": {
                                                 "name": {"type": "string"},
                                                 "type": {"type": "string",
                                                          "enum": ["text","number","textarea","camera","photo"]},
                                                 "label": {"type": "string"},
                                                 "required": {"type": "boolean"},
                                                 "hint": {"type": "string"},
                                             }}},
                        "deadline": {"type": "number",
                                     "description": "Unix timestamp after which task is overdue"},
                    },
                },
            },
            # ── poll ─────────────────────────────────────────────────────────────
            "human://{node}/task/poll": {
                "uri": "human://{node}/task/poll",
                "kind": "query",
                "adapter": "local-function",
                "python": {"type": "python", "module": _MOD, "export": "poll_task"},
                "policy": {"allowExecute": True},
                "meta": {"connector": "human", "label": "Poll task state (pending|done|declined)"},
                "inputSchema": {
                    "type": "object",
                    "required": ["taskId"],
                    "additionalProperties": True,
                    "properties": {"taskId": {"type": "string"}},
                },
            },
            # ── resolve ──────────────────────────────────────────────────────────
            "human://{node}/task/resolve": {
                "uri": "human://{node}/task/resolve",
                "kind": "command",
                "adapter": "local-function",
                "python": {"type": "python", "module": _MOD, "export": "resolve_task"},
                "policy": {"allowExecute": True},
                "meta": {"connector": "human", "label": "Worker submits task outcome (Done / Decline)"},
                "inputSchema": {
                    "type": "object",
                    "required": ["taskId"],
                    "additionalProperties": True,
                    "properties": {
                        "taskId": {"type": "string"},
                        "outcome": {"type": "string", "enum": ["done", "declined"], "default": "done"},
                        "by": {"type": "string"},
                        "note": {"type": "string"},
                        "proofPath": {"type": "string"},
                    },
                },
            },
            # ── claim ────────────────────────────────────────────────────────────
            "human://{node}/task/claim": {
                "uri": "human://{node}/task/claim",
                "kind": "command",
                "adapter": "local-function",
                "python": {"type": "python", "module": _MOD, "export": "claim_task"},
                "policy": {"allowExecute": True},
                "meta": {"connector": "human", "label": "Reserve task for a specific worker (multi-worker queues)"},
                "inputSchema": {
                    "type": "object",
                    "required": ["taskId"],
                    "additionalProperties": True,
                    "properties": {
                        "taskId": {"type": "string"},
                        "by": {"type": "string", "description": "Worker identifier"},
                    },
                },
            },
            # ── cancel ───────────────────────────────────────────────────────────
            "human://{node}/task/cancel": {
                "uri": "human://{node}/task/cancel",
                "kind": "command",
                "adapter": "local-function",
                "python": {"type": "python", "module": _MOD, "export": "cancel_task"},
                "policy": {"allowExecute": True},
                "meta": {"connector": "human", "label": "Cancel a pending task (reversibility / cleanup)"},
                "inputSchema": {
                    "type": "object",
                    "required": ["taskId"],
                    "additionalProperties": True,
                    "properties": {
                        "taskId": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
            # ── precondition grant ───────────────────────────────────────────────
            "human://{node}/grant/satisfy": {
                "uri": "human://{node}/grant/satisfy",
                "kind": "command",
                "adapter": "local-function",
                "python": {"type": "python", "module": _MOD, "export": "satisfy_precondition"},
                "policy": {"allowExecute": True},
                "meta": {"connector": "human", "label": "Per-env grant (Phase 6 provider); recalled on matching twin"},
                "inputSchema": {
                    "type": "object",
                    "required": ["need"],
                    "additionalProperties": True,
                    "properties": {
                        "node": _SCHEMA_NODE,
                        "need": {
                            "type": "object",
                            "required": ["what"],
                            "properties": {
                                "kind": {"type": "string", "default": "acquire"},
                                "what": {"type": "string"},
                                "scope": {"type": "string", "enum": ["per-env"], "default": "per-env"},
                            },
                        },
                        "envProfile": {"type": "object"},
                        "taskId": {"type": "string"},
                    },
                },
            },
        },
    }
