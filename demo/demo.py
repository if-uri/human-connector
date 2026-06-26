"""demo.py — run the human/machine/robot fulfillment cell end to end.

  python demo/demo.py                # two runs: 2nd recalls the per-env grant
  python demo/demo.py --fail-last    # one run, orders API fails -> reversible rollback
  python demo/demo.py --serve        # start the human surface; resolve tasks by tapping

What to watch:
  * step 'operator-grant' (per-env) is asked on run #1, RECALLED on run #2 (human not asked).
  * steps 'safety-clear' and 'inspect-seal' (per-instance) are asked EVERY run.
  * on failure, the robot move and the human seal are undone via their inverses.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import _shim as shim  # noqa: E402  (same dir)
from urirun_connector_human import surface  # noqa: E402
from urirun_connector_human.connector import MEMORY, STORE  # noqa: E402

FLOW = json.loads((ROOT / "flows" / "fulfillment_cell.flow.json").read_text())


def reset_state() -> None:
    """Clear tasks + twin memory so the first-run / second-run story is reproducible."""
    for p in [STORE.path, MEMORY.path]:
        try:
            Path(p).unlink()
        except FileNotFoundError:
            pass
    STORE._init()
    MEMORY._data = {"episodes": {}, "proofs": {}}


def start_surface(port: int = 8788) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("0.0.0.0", port), surface.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def run_default() -> None:
    reset_state()
    bus = shim.EventBus(STORE)
    stop = shim.start_auto_worker(STORE)

    print("=" * 74)
    print("RUN #1 — first time on this twin (operator must grant; humans do the work)")
    print("=" * 74)
    shim.run_flow(FLOW, bus, MEMORY, STORE)

    print("\n" + "=" * 74)
    print("RUN #2 — same intent, same environment (twin remembers the per-env grant)")
    print("=" * 74)
    shim.run_flow(FLOW, bus, MEMORY, STORE)

    stop.set()
    print("\n" + "-" * 74)
    print("Takeaway: the per-env grant was asked once and RECALLED on run #2;")
    print("the per-instance safety + seal were asked again both times (correct & safe).")


def run_fail_last() -> None:
    reset_state()
    bus = shim.EventBus(STORE)
    stop = shim.start_auto_worker(STORE)
    print("=" * 74)
    print("FAILURE RUN — the orders/carrier API rejects the shipment at the last step")
    print("=" * 74)
    out = shim.run_flow(FLOW, bus, MEMORY, STORE, fail_orders=True)
    time.sleep(0.6)  # let the auto-worker resolve the human UNDO task
    bus.drain()
    stop.set()
    print(f"\nResult: {out['status']} at step '{out.get('failedAt')}'. "
          "Robot move and human seal were rolled back via their inverses.")


def run_serve(port: int) -> None:
    reset_state()
    httpd = start_surface(port)
    bus = shim.EventBus(STORE)
    print("=" * 74)
    print(f"LIVE — human surface at http://localhost:{port}/?node=cell-a")
    print("Open it (phone or browser) and resolve each task as it appears.")
    print("=" * 74)

    # Manual mode: no auto-worker. The driver awaits real taps via the surface.
    shim.run_flow(FLOW, bus, MEMORY, STORE)
    print("\nFlow complete. Re-run and refresh the page to see the per-env grant recalled.")
    httpd.shutdown()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fail-last", action="store_true",
                    help="inject a failure at the final step to show reversible rollback")
    ap.add_argument("--serve", action="store_true",
                    help="start the human web surface and resolve tasks by tapping")
    ap.add_argument("--port", type=int, default=8788)
    args = ap.parse_args(argv)

    if args.serve:
        run_serve(args.port)
    elif args.fail_last:
        run_fail_last()
    else:
        run_default()


if __name__ == "__main__":
    main()
