---
name: ifuri-contract-layer-state
description: ifURI contract-layer invariant-enforcement state and known gaps (verified 2026-06-27)
metadata:
  type: project
---

The ifURI contract kernel was extracted from the `urirun` monorepo into the sibling repo `if-uri/urirun-contract` (package `urirun_contract`). In `urirun`, `urirun_connectors_toolkit/contract_*.py` are now thin re-export shims (`from urirun_contract... import *`). Related extracted siblings: `urirun-connector-twin` (`urirun_twin`), `urirun-connector-kvm`. They're editable-installed; if tests fail with `ModuleNotFoundError: No module named 'urirun_node/urirun_contract/...'`, run `pip install -e` of the sibling repos **before** `urirun/adapters/python` (its pyproject deps on the unpublished `urirun-contract>=0.1.0`, so order matters).

Verified state of the doc's two "Do potwierdzenia" invariants:
- **Single-source gates** (check_single_source / regen-check / lint_handler) are wired into the default path **only in the `urirun-contract` repo** (`make check` → `ci/pre_commit.sh`, `make single-source`, CI `contract.yml`, pre-commit hook). The **`urirun` monorepo has no `make check`/pre-commit and no flota-lint** ("mutating route without contracts.py = FAIL" does not exist yet). Minor: urirun-contract CI runs steps individually, not via `make check`, so they can drift.
- **Reversibility single-source (invariant #3):** now has a blessed engine-side builder `urirun_twin.reversible.schema_from_contracts()` (commit 7f05c8c) delegating to `callspecs_from_contracts`, with a test proving `ReversibleProcess` acts on contract-derived CallSpecs. **Not yet migrated in production:** the twin connector's `urirun_connector_twin/planner.py:_step_reversible` still uses a hard-coded `_REVERSIBLE_TABLE` (parallel declaration), and no production `Connector.schema()` impl exists. Main remaining contract work is flota coverage (~3 of ~40 connectors have contracts.py).

See [[urirun-cdp-login-autonomy]] for the autonomy/daemon-restart context in the same repo.
