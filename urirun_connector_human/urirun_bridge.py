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

VERSION = "urirun.bindings.v2"

# --------------------------------------------------------------------------- #
# Handler wrappers — accept **payload so _handler_kwargs passes everything through
# --------------------------------------------------------------------------- #

def request_task(**payload) -> dict:
    return handlers.request_task(payload, STORE, MEMORY)


def poll_task(**payload) -> dict:
    return handlers.poll_task(payload, STORE, MEMORY)


def resolve_task(**payload) -> dict:
    return handlers.resolve_task(payload, STORE, MEMORY)


def cancel_task(**payload) -> dict:
    return handlers.cancel_task(payload, STORE, MEMORY)


def satisfy_precondition(**payload) -> dict:
    return handlers.satisfy_precondition(payload, STORE, MEMORY)


# --------------------------------------------------------------------------- #
# Bindings document — returned by the urirun.bindings entry point
# --------------------------------------------------------------------------- #

_MOD = __name__  # urirun_connector_human.urirun_bridge

_SCHEMA_NODE = {"type": "string", "description": "Physical cell, e.g. cell-a"}
_SCHEMA_SCOPE = {"type": "string", "enum": ["per-instance", "per-env"], "default": "per-instance"}
_SCHEMA_KIND = {"type": "string", "enum": ["action", "judgement", "safety", "grant"], "default": "action"}


def urirun_bindings() -> dict:
    """Called by the urirun.bindings entry point loader at node startup."""
    return {
        "version": VERSION,
        "bindings": {
            "human://{node}/task/command/request": {
                "uri": "human://{node}/task/command/request",
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
                        "kind": _SCHEMA_KIND,
                        "env": {"type": "string"},
                        "inverse": {"type": "object"},
                    },
                },
            },
            "human://{node}/task/query/poll": {
                "uri": "human://{node}/task/query/poll",
                "kind": "query",
                "adapter": "local-function",
                "python": {"type": "python", "module": _MOD, "export": "poll_task"},
                "policy": {"allowExecute": True},
                "meta": {"connector": "human", "label": "Poll a task state (pending|done|declined)"},
                "inputSchema": {
                    "type": "object",
                    "required": ["taskId"],
                    "additionalProperties": True,
                    "properties": {"taskId": {"type": "string"}},
                },
            },
            "human://{node}/task/command/resolve": {
                "uri": "human://{node}/task/command/resolve",
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
            "human://{node}/task/command/cancel": {
                "uri": "human://{node}/task/command/cancel",
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
            "human://{node}/precondition/command/satisfy": {
                "uri": "human://{node}/precondition/command/satisfy",
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
