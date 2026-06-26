"""TaskStore — the durable list of human tasks + a small event log.

It is file-backed (sqlite) on purpose: the connector handlers and the human web
surface run in *different processes*, and both must see the same pending tasks.
In the real urirun tree this responsibility belongs to the node runtime / host_db
(`urirun.host.host_db.add_log`, artifact + check tables); here it is a self-
contained stand-in so the example runs with zero dependencies.

A "human task" is one unit of work that only a person can complete: a physical
action, a judgement, a safety confirmation, or a per-environment grant/login.
The store knows nothing about URIs or envelopes — that lives in `handlers.py`.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB = Path.home() / ".urirun-human" / "tasks.db"

# Lifecycle of a task:  open --(claim)--> claimed --(resolve)--> done | declined
#                        open ----------------(cancel)---------> cancelled
OPEN_STATES = ("open", "claimed")


def _now() -> float:
    return time.time()


def _new_id(prefix: str = "ht") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class TaskStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = Path(db_path or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    # --- connection helpers -------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id          TEXT PRIMARY KEY,
                    node        TEXT,
                    scope       TEXT,
                    kind        TEXT,
                    title       TEXT,
                    instruction TEXT,
                    env         TEXT,
                    payload     TEXT,
                    status      TEXT,
                    created     REAL,
                    updated     REAL,
                    claimed_by  TEXT,
                    resolved_by TEXT,
                    outcome     TEXT,
                    result      TEXT,
                    proof_path  TEXT,
                    inverse     TEXT,
                    options     TEXT,   -- JSON: ["Option A","Option B"] for kind=choice
                    fields      TEXT,   -- JSON: [{name,type,label,required}] for kind=form
                    deadline    REAL    -- unix timestamp; null = no deadline
                )
                """
            )
            # Migrate existing DBs that predate the new columns
            for col, defn in [
                ("options",  "TEXT"),
                ("fields",   "TEXT"),
                ("deadline", "REAL"),
            ]:
                try:
                    c.execute(f"ALTER TABLE tasks ADD COLUMN {col} {defn}")
                except sqlite3.OperationalError:
                    pass  # column already exists

            c.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts      REAL,
                    node    TEXT,
                    task_id TEXT,
                    type    TEXT,
                    data    TEXT
                )
                """
            )

    # --- tasks --------------------------------------------------------------
    def create_task(
        self,
        *,
        node: str,
        title: str,
        instruction: str,
        env: str,
        scope: str = "per-instance",
        kind: str = "action",
        payload: Optional[dict] = None,
        inverse: Optional[dict] = None,
        options: Optional[list] = None,
        fields: Optional[list] = None,
        deadline: Optional[float] = None,
    ) -> dict:
        tid = _new_id()
        now = _now()
        if not isinstance(env, str):
            env = json.dumps(env)
        with self._conn() as c:
            c.execute(
                "INSERT INTO tasks (id,node,scope,kind,title,instruction,env,payload,"
                "status,created,updated,inverse,options,fields,deadline)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tid, node, scope, kind, title, instruction, env,
                    json.dumps(payload or {}), "open", now, now,
                    json.dumps(inverse) if inverse else None,
                    json.dumps(options) if options else None,
                    json.dumps(fields) if fields else None,
                    deadline,
                ),
            )
        self.append_event(node=node, task_id=tid, type="human.task.requested",
                          data={"title": title, "scope": scope, "kind": kind, "env": env})
        return self.get_task(tid)

    def get_task(self, task_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def list_open(self, node: str | None = None) -> list[dict]:
        q = "SELECT * FROM tasks WHERE status IN (?,?)"
        args: list[Any] = list(OPEN_STATES)
        if node:
            q += " AND node=?"
            args.append(node)
        q += " ORDER BY created ASC"
        with self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [self._row_to_task(r) for r in rows]

    def claim(self, task_id: str, by: str) -> dict | None:
        with self._conn() as c:
            c.execute(
                "UPDATE tasks SET status='claimed', claimed_by=?, updated=? "
                "WHERE id=? AND status='open'",
                (by, _now(), task_id),
            )
        t = self.get_task(task_id)
        if t and t["status"] == "claimed":
            self.append_event(node=t["node"], task_id=task_id,
                              type="human.task.claimed", data={"by": by})
        return t

    def resolve(
        self,
        task_id: str,
        *,
        by: str,
        outcome: str,
        note: str = "",
        result: Optional[dict] = None,
        proof_path: Optional[str] = None,
    ) -> dict | None:
        status = "done" if outcome not in ("declined", "cancelled") else outcome
        with self._conn() as c:
            c.execute(
                "UPDATE tasks SET status=?, outcome=?, resolved_by=?, result=?, "
                "proof_path=?, updated=? WHERE id=? AND status IN ('open','claimed')",
                (
                    status, outcome, by,
                    json.dumps({"note": note, **(result or {})}),
                    proof_path, _now(), task_id,
                ),
            )
        t = self.get_task(task_id)
        if t and t["status"] in ("done", "declined"):
            self.append_event(node=t["node"], task_id=task_id,
                              type="human.task.resolved",
                              data={"by": by, "outcome": outcome})
        return t

    def cancel(self, task_id: str, reason: str = "") -> dict | None:
        with self._conn() as c:
            c.execute(
                "UPDATE tasks SET status='cancelled', updated=? "
                "WHERE id=? AND status IN ('open','claimed')",
                (_now(), task_id),
            )
        t = self.get_task(task_id)
        if t and t["status"] == "cancelled":
            self.append_event(node=t["node"], task_id=task_id,
                              type="human.task.cancelled", data={"reason": reason})
        return t

    # --- events -------------------------------------------------------------
    def append_event(self, *, node: str, task_id: str, type: str, data: dict) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO events (ts,node,task_id,type,data) VALUES (?,?,?,?,?)",
                (_now(), node, task_id, type, json.dumps(data)),
            )

    def events_since(self, seq: int = 0, node: str | None = None) -> list[dict]:
        q = "SELECT * FROM events WHERE seq>?"
        args: list[Any] = [seq]
        if node:
            q += " AND node=?"
            args.append(node)
        q += " ORDER BY seq ASC"
        with self._conn() as c:
            rows = c.execute(q, args).fetchall()
        return [
            {"seq": r["seq"], "ts": r["ts"], "node": r["node"],
             "taskId": r["task_id"], "type": r["type"], "data": json.loads(r["data"])}
            for r in rows
        ]

    def latest_seq(self, node: str | None = None) -> int:
        q = "SELECT MAX(seq) FROM events"
        args: list[Any] = []
        if node:
            q += " WHERE node=?"
            args.append(node)
        with self._conn() as c:
            row = c.execute(q, args).fetchone()
        return (row[0] or 0)

    # --- mapping ------------------------------------------------------------
    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict:
        keys = row.keys()
        return {
            "id": row["id"],
            "node": row["node"],
            "scope": row["scope"],
            "kind": row["kind"],
            "title": row["title"],
            "instruction": row["instruction"] or "",
            "env": row["env"],
            "payload": json.loads(row["payload"] or "{}"),
            "status": row["status"],
            "created": row["created"],
            "updated": row["updated"],
            "claimedBy": row["claimed_by"],
            "resolvedBy": row["resolved_by"],
            "outcome": row["outcome"],
            "result": json.loads(row["result"]) if row["result"] else None,
            "proofPath": row["proof_path"],
            "inverse": json.loads(row["inverse"]) if row["inverse"] else None,
            "options": json.loads(row["options"]) if "options" in keys and row["options"] else None,
            "fields": json.loads(row["fields"]) if "fields" in keys and row["fields"] else None,
            "deadline": row["deadline"] if "deadline" in keys else None,
        }
