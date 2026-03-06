"""Central Sheet - SQLite-backed portfolio database."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone


_SCHEMA = """
-- Discovered agents from marketplace
CREATE TABLE IF NOT EXISTS agents (
    agent_id    TEXT PRIMARY KEY,
    name        TEXT,
    description TEXT,
    url         TEXT,
    plan_id     TEXT,
    tags        TEXT,           -- JSON array
    pricing     TEXT,           -- JSON: {tier_name: {credits, description}}
    category    TEXT,           -- from Discovery API: 'DeFi', 'AI/ML', etc.
    team_name   TEXT,           -- from Discovery API: hackathon team name
    first_seen  TEXT,
    last_seen   TEXT,
    status      TEXT DEFAULT 'new'  -- new | probed | evaluated | dead
);

-- Raw purchase results from probes
CREATE TABLE IF NOT EXISTS probes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        TEXT NOT NULL,
    query           TEXT,
    response        TEXT,
    credits_spent   INTEGER DEFAULT 0,
    latency_ms      REAL,
    response_bytes  INTEGER,
    http_status     INTEGER,
    error           TEXT,          -- null if success
    timestamp       TEXT,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);

-- Sub-agent evaluation results (pluggable — each sub-agent writes its own rows)
CREATE TABLE IF NOT EXISTS evaluations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL,
    probe_id    INTEGER,            -- nullable: some evals span multiple probes
    evaluator   TEXT NOT NULL,       -- sub-agent name: 'quality_judge', 'comparative', etc.
    metrics     TEXT NOT NULL,       -- JSON: whatever the sub-agent outputs
    summary     TEXT,                -- human-readable summary from LLM
    timestamp   TEXT,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id),
    FOREIGN KEY (probe_id) REFERENCES probes(id)
);

-- Credit ledger for P&L tracking
CREATE TABLE IF NOT EXISTS ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    direction   TEXT NOT NULL,       -- 'out' or 'in'
    agent_id    TEXT,
    credits     INTEGER NOT NULL,
    purpose     TEXT,                -- 'probe', 'consulting_upstream', 'consulting_revenue'
    detail      TEXT,                -- query text or client request summary
    timestamp   TEXT
);

-- Portfolio: aggregated view over agents, probes, evaluations
CREATE VIEW IF NOT EXISTS portfolio AS
SELECT
    a.agent_id,
    a.name,
    a.url,
    a.plan_id,
    a.status,
    a.pricing,
    COUNT(DISTINCT p.id) as probe_count,
    AVG(p.credits_spent) as avg_cost,
    AVG(p.latency_ms) as avg_latency,
    SUM(p.credits_spent) as total_spent,
    COUNT(DISTINCT e.id) as eval_count,
    MAX(p.timestamp) as last_probe,
    MAX(e.timestamp) as last_eval
FROM agents a
LEFT JOIN probes p ON a.agent_id = p.agent_id AND p.error IS NULL
LEFT JOIN evaluations e ON a.agent_id = e.agent_id
GROUP BY a.agent_id;
"""


def _now() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


class CentralSheet:
    """Thread-safe SQLite wrapper for agent portfolio management."""

    def __init__(self, db_path: str = "portfolio.db"):
        """Initialize the central sheet with SQLite database."""
        self._db_path = db_path
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        """Get or create thread-local connection with WAL mode."""
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            self._local.conn = conn
        return self._local.conn

    # ── Agents ──────────────────────────────────────────────

    def write_agent(
        self,
        agent_id: str,
        name: str,
        url: str,
        plan_id: str,
        pricing: dict | None = None,
        tags: list[str] | None = None,
        description: str = "",
        category: str = "",
        team_name: str = "",
    ) -> None:
        """Write or update an agent record."""
        now = _now()
        self._conn().execute(
            """INSERT INTO agents (agent_id, name, description, url, plan_id,
                                   tags, pricing, category, team_name,
                                   first_seen, last_seen, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
               ON CONFLICT(agent_id) DO UPDATE SET
                   last_seen=?, pricing=COALESCE(?, pricing),
                   url=COALESCE(?, url)""",
            (
                agent_id,
                name,
                description,
                url,
                plan_id,
                json.dumps(tags or []),
                json.dumps(pricing or {}),
                category,
                team_name,
                now,
                now,
                now,
                json.dumps(pricing) if pricing else None,
                url,
            ),
        )
        self._conn().commit()

    def read_agents(self, status: str | None = None) -> list[dict]:
        """Read all agents, optionally filtered by status."""
        if status:
            rows = self._conn().execute(
                "SELECT * FROM agents WHERE status = ?", (status,)
            ).fetchall()
        else:
            rows = self._conn().execute("SELECT * FROM agents").fetchall()
        return [dict(r) for r in rows]

    def update_agent_status(self, agent_id: str, status: str) -> None:
        """Update agent status."""
        self._conn().execute(
            "UPDATE agents SET status = ? WHERE agent_id = ?",
            (status, agent_id),
        )
        self._conn().commit()

    # ── Probes ──────────────────────────────────────────────

    def write_probe(
        self,
        agent_id: str,
        query: str,
        response: str,
        credits_spent: int,
        latency_ms: float = 0.0,
        response_bytes: int = 0,
        http_status: int = 200,
        error: str | None = None,
    ) -> int:
        """Write a probe result and return probe ID."""
        cur = self._conn().execute(
            """INSERT INTO probes
               (agent_id, query, response, credits_spent, latency_ms,
                response_bytes, http_status, error, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                agent_id,
                query,
                response,
                credits_spent,
                latency_ms,
                response_bytes,
                http_status,
                error,
                _now(),
            ),
        )
        self._conn().commit()
        return cur.lastrowid

    def read_probes(self, agent_id: str | None = None, limit: int = 50) -> list[dict]:
        """Read probe results, optionally filtered by agent_id."""
        if agent_id:
            rows = self._conn().execute(
                "SELECT * FROM probes WHERE agent_id = ? ORDER BY timestamp DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        else:
            rows = self._conn().execute(
                "SELECT * FROM probes ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Evaluations ─────────────────────────────────────────

    def write_evaluation(
        self,
        agent_id: str,
        evaluator: str,
        metrics: dict,
        summary: str = "",
        probe_id: int | None = None,
    ) -> int:
        """Write an evaluation result and return evaluation ID."""
        cur = self._conn().execute(
            """INSERT INTO evaluations
               (agent_id, probe_id, evaluator, metrics, summary, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (agent_id, probe_id, evaluator, json.dumps(metrics), summary, _now()),
        )
        self._conn().commit()
        return cur.lastrowid

    def read_evaluations(
        self,
        agent_id: str | None = None,
        evaluator: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Read evaluations, optionally filtered by agent_id and/or evaluator."""
        clauses, params = [], []
        if agent_id:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if evaluator:
            clauses.append("evaluator = ?")
            params.append(evaluator)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn().execute(
            f"SELECT * FROM evaluations {where} ORDER BY timestamp DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["metrics"] = json.loads(d["metrics"])
            result.append(d)
        return result

    # ── Ledger ──────────────────────────────────────────────

    def write_ledger(
        self,
        direction: str,
        credits: int,
        purpose: str,
        agent_id: str = "",
        detail: str = "",
    ) -> None:
        """Write a ledger entry."""
        self._conn().execute(
            """INSERT INTO ledger
               (direction, agent_id, credits, purpose, detail, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (direction, agent_id, credits, purpose, detail, _now()),
        )
        self._conn().commit()

    def get_pnl(self) -> dict:
        """Get profit and loss summary."""
        row = self._conn().execute(
            """SELECT
                COALESCE(SUM(CASE WHEN direction='in'  THEN credits END), 0) as revenue,
                COALESCE(SUM(CASE WHEN direction='out' THEN credits END), 0) as spent
               FROM ledger"""
        ).fetchone()
        return {
            "revenue": row["revenue"],
            "spent": row["spent"],
            "margin": row["revenue"] - row["spent"],
        }

    # ── Portfolio ───────────────────────────────────────────

    def read_portfolio(self) -> list[dict]:
        """Read the portfolio view."""
        rows = self._conn().execute("SELECT * FROM portfolio").fetchall()
        return [dict(r) for r in rows]

    def get_top_agents(self, limit: int = 10) -> list[dict]:
        """Get top performing agents by eval count and cost."""
        rows = self._conn().execute(
            "SELECT * FROM portfolio WHERE probe_count > 0 ORDER BY eval_count DESC, avg_cost ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
