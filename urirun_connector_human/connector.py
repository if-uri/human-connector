"""Wiring for the `human://` connector.

`ROUTES` is the single source of truth (URI template -> handler). Two consumers
derive from it:

  * `build_connector()` builds a real `urirun.Connector` when urirun is installed,
    so the node serves these routes over `/run` like any other connector.
  * `local_dispatch()` lets the bundled runtime shim (demo/) dispatch the very
    same handler code with zero dependencies.

Module-level `STORE` / `MEMORY` are file-backed so the separate human web surface
(`surface.py`, a different process) shares state. In a real node these would be
provided by the node runtime / host_db instead of being constructed here.
"""
from __future__ import annotations

import re
from typing import Callable

from . import handlers
from .episode import TwinMemory
from .store import TaskStore

STORE = TaskStore()
MEMORY = TwinMemory()


def _bind(fn: Callable) -> Callable:
    """Adapt a (payload, store, memory) handler to a (payload) -> envelope call."""
    def call(payload: dict) -> dict:
        return fn(payload or {}, STORE, MEMORY)
    call.__name__ = fn.__name__
    return call


# URI template -> (kind, handler). Authority `{node}` is filled by the caller.
ROUTES: dict[str, tuple[str, Callable]] = {
    "human://{node}/task/command/request":         ("command", _bind(handlers.request_task)),
    "human://{node}/task/query/poll":              ("query",   _bind(handlers.poll_task)),
    "human://{node}/task/command/resolve":         ("command", _bind(handlers.resolve_task)),
    "human://{node}/task/command/cancel":          ("command", _bind(handlers.cancel_task)),
    "human://{node}/precondition/command/satisfy": ("command", _bind(handlers.satisfy_precondition)),
}

_TEMPLATE_RE = {
    uri: re.compile("^" + re.escape(uri).replace(r"\{node\}", r"[^/]+") + "$")
    for uri in ROUTES
}


def resolve_handler(uri: str) -> Callable | None:
    """Match a concrete URI (human://cell-a/task/command/request) to its handler."""
    for template, rx in _TEMPLATE_RE.items():
        if rx.match(uri):
            return ROUTES[template][1]
    return None


def local_dispatch(uri: str, payload: dict) -> dict:
    """Dispatch a concrete human:// URI through the matching handler.

    This is what the demo runtime shim calls. In real urirun the node's
    dispatcher does the equivalent after resolving the route from the registry.
    """
    fn = resolve_handler(uri)
    if fn is None:
        return {"ok": False, "connector": "human",
                "error": {"type": "RouteNotFound", "message": uri, "uri": uri}}
    return fn(payload or {})


def build_connector():
    """Return a `urirun.Connector` if urirun is importable, else None.

    NOTE: the exact decorator names on `Connector` (`.command` / `.handler`) may
    differ across urirun versions; if so, adjust here and run
    `urirun connectors lint` / `sync-manifest` to reconcile bindings.json.
    """
    try:
        from urirun import Connector  # type: ignore
    except Exception:
        return None
    c = Connector("human")
    for uri, (kind, fn) in ROUTES.items():
        register = getattr(c, kind, None) or getattr(c, "handler")
        register(uri)(fn)
    return c
