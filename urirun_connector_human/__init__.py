"""urirun-connector-human — a first-class `human://` capability for urirun.

Turns "ask a person to do / confirm / authorise something" into an addressable
URI step, so a flow can interleave machine, robot and human work uniformly:
the thin-driver dispatches `human://...` exactly like `cdp://...` or `robot://...`.

See README.md for the URI surface and how it maps onto the refactor roadmap
(Faza 1 recall, Faza 2 flow://, Faza 3 sesje/skille, Faza 6 providers).
"""
from .connector import ROUTES, build_connector, local_dispatch, resolve_handler
from .episode import TwinMemory, environment_fingerprint, intent_signature
from .store import TaskStore

__all__ = [
    "ROUTES",
    "build_connector",
    "local_dispatch",
    "resolve_handler",
    "TaskStore",
    "TwinMemory",
    "intent_signature",
    "environment_fingerprint",
]
__version__ = "0.1.0"
