"""TwinMemory — the part that lets a human be asked *once per environment*.

This mirrors the real `urirun.node.reversible.TwinMemory` /
`urirun.node.twin_store` surface, scoped to what this connector needs:

  * `intent_signature(goal)`        -> stable hash of an intent
  * `environment_fingerprint(env)`  -> stable hash of an environment profile
  * known-good episodes keyed by (intent_sig x env_fp)
  * per-environment human PROOFS keyed by (need x env_fp)

The crucial, honest distinction (see REFACTOR_ROADMAP Faza 3 "uczciwa granica"):

  per-env   inputs  (a grant / login / calibration / standing judgement that is a
                     fact about the *environment*) are recall-cacheable. Asked once
                     per env; replayed on a matching twin; re-asked only on drift.

  per-instance actions (seal THIS box, confirm THIS area is clear) are NEVER
                     recalled. They are part of the audit trail but must be
                     performed again every run. Caching them would be unsafe.

So "the twin remembers" applies to per-env facts, not to physical per-instance work.
That is the whole safety boundary of reusing human input.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

DEFAULT_PATH = Path.home() / ".urirun-human" / "twin_memory.json"


def _sha(text: str, prefix: str, length: int = 12) -> str:
    return f"{prefix}_{hashlib.sha1(text.encode()).hexdigest()[:length]}"


def intent_signature(goal: str) -> str:
    """Stable signature for an intent (normalised). Mirrors urirun's intent_signature."""
    norm = " ".join((goal or "").lower().split())
    return _sha(norm, "intent")


def environment_fingerprint(profile: dict | str) -> str:
    """Stable fingerprint for an environment profile.

    A real twin fingerprints the live surface (resolution, session, installed
    backends...). Here `profile` is whatever the host knows about the physical
    cell — its label plus any state that, if changed, should force re-asking the
    human (a different shift, a recalibrated arm, a swapped operator policy...).
    """
    if isinstance(profile, str):
        profile = {"env": profile}
    canon = json.dumps(profile, sort_keys=True)
    return _sha(canon, "env")


class TwinMemory:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or DEFAULT_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                pass
        return {"episodes": {}, "proofs": {}}

    def _flush(self) -> None:
        self.path.write_text(json.dumps(self._data, indent=2))

    # --- known-good episodes (Faza 1: skip re-work on a matching twin) -------
    def remember_episode(self, intent_sig: str, env_fp: str, episode: dict) -> None:
        self._data["episodes"][f"{intent_sig}@{env_fp}"] = {
            "intentSig": intent_sig, "envFp": env_fp,
            "episode": episode, "ts": time.time(),
        }
        self._flush()

    def known_good_episode(self, intent_sig: str, env_fp: str) -> Optional[dict]:
        rec = self._data["episodes"].get(f"{intent_sig}@{env_fp}")
        return rec["episode"] if rec else None

    # --- per-environment human proofs (Faza 6: asked once per env) -----------
    def remember_proof(self, need_key: str, env_fp: str, proof: dict) -> None:
        self._data["proofs"][f"{need_key}@{env_fp}"] = {
            "need": need_key, "envFp": env_fp, "proof": proof, "ts": time.time(),
        }
        self._flush()

    def recall_proof(self, need_key: str, env_fp: str) -> Optional[dict]:
        rec = self._data["proofs"].get(f"{need_key}@{env_fp}")
        return rec["proof"] if rec else None

    def drift(self, need_key: str, env_fp: str) -> str:
        """'matches-known-good' if a proof exists for this exact env fingerprint,
        'no-known-good' if nothing is remembered, 'drift' if remembered under a
        *different* fingerprint (env changed -> re-ask the human).
        """
        if self.recall_proof(need_key, env_fp) is not None:
            return "matches-known-good"
        for key in self._data["proofs"]:
            if key.startswith(f"{need_key}@"):
                return "drift"
        return "no-known-good"

    def forget(self, need_key: str | None = None) -> None:
        """Test/ops helper: drop remembered proofs (and episodes if no key given)."""
        if need_key is None:
            self._data = {"episodes": {}, "proofs": {}}
        else:
            self._data["proofs"] = {
                k: v for k, v in self._data["proofs"].items()
                if not k.startswith(f"{need_key}@")
            }
        self._flush()
