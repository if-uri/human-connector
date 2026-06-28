---
name: urirun-cdp-login-autonomy
description: Why urirun CDP login-gated flows (e.g. LinkedIn post) fail and what makes them work
metadata:
  type: project
---

In the urirun repo (`/home/tom/github/if-uri/urirun`, separate git repo from human-connector), login-gated CDP automation ("publish a LinkedIn post" etc.) fails at the `kvm://host/ui/query/verify` gate with `required text not found on screen`. Root cause: the flow's `cdp/session/command/ensure` step used `user_data_dir=<live Chrome profile>`, which fights Chrome's SingletonLock and copies no cookies (`authCopied:[]`) → login wall.

**Fix lives in `adapters/python/urirun_flow/flow_planner.py`** — `_rewrite_cdp_profile_for_auth()` rewrites `user_data_dir`→`copy_from=<profile root>` (must be `~/.config/google-chrome`, NOT the `/Default` leaf — `_AUTH_FILES` in `urirun_cdp/cdp.py` resolve `Default/Cookies` against the root). `copy_from` clones auth into a dedicated `/tmp` CDP profile (lock-safe + logged in). Branch: `fix/cdp-login-profile-autonomy` (commit fc5fd35).

**Two things code can't fix (human-gated):**
1. The running `urirun host` daemon imports editable-install source — it must be **restarted** to pick up source edits. Empty `recovery:[]` in flow output is the tell that the daemon is running stale code.
2. `copy_from` only clones *existing* cookies — the `~/.config/google-chrome` profile must already be logged into the target service.

Also: `kvm://host/env/query/profile` + `surface/query/current` unreachable → run `urirun host ensure host kvm`; without them the twin can't auto-detect the login wall to escalate the diagnosis. The analysis files under `urirun/project/*.toon.yaml` are code2llm output and go stale fast — regenerate before trusting them.
