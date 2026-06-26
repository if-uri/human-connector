"""Envelope helpers.

A connector's job is to return a *portable JSON envelope*. urirun consumers read
documented fields off that dict — `ok`, `error`, `verification`, `next`, `inverse`,
`artifact`, `status` — so the dict shape IS the contract (see docs/URI_OBJECTS.md
and docs/DECISION_LOOP.md). These helpers just make that shape consistent.

In the real tree you may instead `from urirun import ok, fail` and add fields; the
field names below match what the host/flow_thin/decision_loop already consume.
"""
from __future__ import annotations

from typing import Any, Optional

CONNECTOR = "human"


def ok(**fields: Any) -> dict:
    env: dict = {"ok": True, "connector": CONNECTOR}
    env.update(fields)
    return env


def fail(error_type: str, message: str, uri: Optional[str] = None, **fields: Any) -> dict:
    error: dict = {"type": error_type, "message": message}
    if uri:
        error["uri"] = uri
    env: dict = {"ok": False, "connector": CONNECTOR, "error": error}
    env.update(fields)
    return env


def verification(contract: str, ok_: bool, expected: Any, actual: Any, **extra: Any) -> dict:
    block = {"contract": contract, "ok": bool(ok_), "expected": expected, "actual": actual}
    block.update(extra)
    return block
