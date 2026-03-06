# Autonomous Agent Portfolio Manager — V3 Implementation Plan

> AI McKinsey meets Berkshire Hathaway. We evaluate every agent in the marketplace, maintain a live ROI-ranked portfolio, and sell consulting where we buy from the best agents on the client's behalf.

---

## Stack

| Layer | Choice | Reason |
|---|---|---|
| Agent framework | Strands Agents SDK (Python) | `invocation_state` passes x402 token natively; `@requires_payment` is a first-class decorator; `stream_async()` for SSE; all starter-kit code is Strands-first |
| LLM | Claude Sonnet 4.6 via Bedrock | `BedrockModel(model_id="us.anthropic.claude-sonnet-4-6")` — inference profile, hackathon credits cover it |
| Payment protocol | x402 | Universal substrate — even A2A's `PaymentsClient` calls `get_x402_access_token()` internally. ~50 lines for full buy flow. |
| Payment SDK | `payments-py` with `[strands, a2a, langchain]` extras | Need `strands` for seller-side `@requires_payment`, `a2a` for discovery/agent cards |
| Web server | FastAPI + Uvicorn on **EC2** (single `t3.medium`) | Simplest path: `ssh` in, `tmux`, `poetry run server`. No container registry, no orchestrator. Upgrade to ECS later if needed. |
| Persistence | **SQLite WAL** on the EC2 instance's EBS volume | Single-process server = SQLite is perfect. Zero setup. Back up with a cron `cp` to S3 if you care. No RDS, no DynamoDB. |

---

## Architecture

```
                     ┌──────────────────────────────┐
                     │       FastAPI Web Server      │
                     │       (main.py)               │
                     │                               │
                     │  POST /data  ← consulting     │
                     │  GET  /pricing                │
                     │  GET  /portfolio              │
                     │  GET  /health                 │
                     └───────┬──────────┬────────────┘
                             │          │
              ┌──────────────┘          └──────────────┐
              ▼                                        ▼
   ┌─────────────────────┐              ┌──────────────────────┐
   │  Consulting Agent   │              │  Scanner (bg task)   │
   │  (Strands + tools)  │              │  asyncio.create_task │
   │                     │              │  every N seconds     │
   │  reads portfolio    │              │                      │
   │  buys upstream      │              │  discovers agents    │
   │  synthesizes        │              │  diffs against sheet │
   │  @requires_payment  │              │  spawns probes       │
   └─────────┬───────────┘              └──────────┬───────────┘
             │                                     │
             │         ┌───────────────┐           │
             ├────────▶│ Central Sheet │◀──────────┤
             │         │  (SQLite WAL) │           │
             │         │               │           │
             │         │ agents        │           │
             │         │ probes        │           │
             │         │ evaluations   │           │
             │         │ ledger        │           │
             │         │ portfolio VIEW│           │
             │         └───────┬───────┘           │
             │                 ▲                   │
             │                 │                   │
             │    ┌────────────┴────────────┐      │
             │    │                         │      │
             ▼    ▼                         ▼      ▼
   ┌──────────────────┐          ┌──────────────────────┐
   │  Probe Runner    │          │  Evaluation Pipeline │
   │  (raw Python)    │          │                      │
   │                  │          │  run_evaluation()    │
   │  stateless       │          │  dispatches to N     │
   │  fire-and-forget │          │  scorer sub-agents   │
   │  calls _impl()   │          │  writes to sheet     │
   │  writes to sheet │          │                      │
   └──────────────────┘          └──────────────────────┘
```

---

## Component 1: Central Sheet (`central_sheet.py`)

**Build first. Everything depends on this.**

SQLite with WAL mode. Every component gets its own `CentralSheet` instance pointing to the same file.

### Schema

```python
# central_sheet.py

import sqlite3
import json
import threading
from datetime import datetime, timezone
from pathlib import Path


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
    return datetime.now(timezone.utc).isoformat()


class CentralSheet:
    """Thread-safe SQLite wrapper. Each instance opens its own connection."""

    def __init__(self, db_path: str = "portfolio.db"):
        self._db_path = db_path
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            conn.executescript(_SCHEMA)
            self._local.conn = conn
        return self._local.conn

    # ── Agents ──────────────────────────────────────────────

    def write_agent(self, agent_id: str, name: str, url: str,
                    plan_id: str, pricing: dict | None = None,
                    tags: list[str] | None = None,
                    description: str = "",
                    category: str = "", team_name: str = "") -> None:
        now = _now()
        self._conn().execute(
            """INSERT INTO agents (agent_id, name, description, url, plan_id,
                                   tags, pricing, category, team_name,
                                   first_seen, last_seen, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new')
               ON CONFLICT(agent_id) DO UPDATE SET
                   last_seen=?, pricing=COALESCE(?, pricing),
                   url=COALESCE(?, url)""",
            (agent_id, name, description, url, plan_id,
             json.dumps(tags or []), json.dumps(pricing or {}),
             category, team_name,
             now, now,
             now, json.dumps(pricing) if pricing else None, url),
        )
        self._conn().commit()

    def read_agents(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._conn().execute(
                "SELECT * FROM agents WHERE status = ?", (status,)
            ).fetchall()
        else:
            rows = self._conn().execute("SELECT * FROM agents").fetchall()
        return [dict(r) for r in rows]

    def update_agent_status(self, agent_id: str, status: str) -> None:
        self._conn().execute(
            "UPDATE agents SET status = ? WHERE agent_id = ?",
            (status, agent_id),
        )
        self._conn().commit()

    # ── Probes ──────────────────────────────────────────────

    def write_probe(self, agent_id: str, query: str, response: str,
                    credits_spent: int, latency_ms: float,
                    response_bytes: int, http_status: int,
                    error: str | None = None) -> int:
        cur = self._conn().execute(
            """INSERT INTO probes
               (agent_id, query, response, credits_spent, latency_ms,
                response_bytes, http_status, error, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent_id, query, response, credits_spent, latency_ms,
             response_bytes, http_status, error, _now()),
        )
        self._conn().commit()
        return cur.lastrowid

    def read_probes(self, agent_id: str | None = None,
                    limit: int = 50) -> list[dict]:
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

    # ── Evaluations (pluggable — any sub-agent writes here) ─

    def write_evaluation(self, agent_id: str, evaluator: str,
                         metrics: dict, summary: str = "",
                         probe_id: int | None = None) -> int:
        cur = self._conn().execute(
            """INSERT INTO evaluations
               (agent_id, probe_id, evaluator, metrics, summary, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (agent_id, probe_id, evaluator, json.dumps(metrics), summary, _now()),
        )
        self._conn().commit()
        return cur.lastrowid

    def read_evaluations(self, agent_id: str | None = None,
                         evaluator: str | None = None,
                         limit: int = 50) -> list[dict]:
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

    def write_ledger(self, direction: str, credits: int,
                     purpose: str, agent_id: str = "",
                     detail: str = "") -> None:
        self._conn().execute(
            """INSERT INTO ledger
               (direction, agent_id, credits, purpose, detail, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (direction, agent_id, credits, purpose, detail, _now()),
        )
        self._conn().commit()

    def get_pnl(self) -> dict:
        row = self._conn().execute(
            """SELECT
                COALESCE(SUM(CASE WHEN direction='in'  THEN credits END), 0) as revenue,
                COALESCE(SUM(CASE WHEN direction='out' THEN credits END), 0) as spent
               FROM ledger"""
        ).fetchone()
        return {"revenue": row["revenue"], "spent": row["spent"],
                "margin": row["revenue"] - row["spent"]}

    # ── Portfolio ───────────────────────────────────────────

    def read_portfolio(self) -> list[dict]:
        rows = self._conn().execute("SELECT * FROM portfolio").fetchall()
        return [dict(r) for r in rows]

    def get_top_agents(self, n: int = 5) -> list[dict]:
        """Top agents by eval count, for consulting."""
        rows = self._conn().execute(
            "SELECT * FROM portfolio WHERE probe_count > 0 ORDER BY eval_count DESC, avg_cost ASC LIMIT ?",
            (n,),
        ).fetchall()
        return [dict(r) for r in rows]
```

**Key design: the `evaluations` table is schema-less on the metrics column.** Each scorer sub-agent writes a JSON blob with whatever metrics it produces. The `evaluator` column tags which sub-agent produced it. This is the extensibility point — add a new scorer, it writes new rows with a new `evaluator` name, existing code doesn't break.

---

## Component 2: Scanner (`scanner.py`)

Background async task. Discovers agents, diffs against known set, spawns probes for new ones.

```python
# scanner.py

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx
from payments_py import Payments

from .central_sheet import CentralSheet

logger = logging.getLogger(__name__)

# ── Discovery API ───────────────────────────────────────────
# Hackathon provides a live discovery endpoint that returns ALL
# registered sellers and buyers in structured JSON.
#
# GET https://nevermined.ai/hackathon/register/api/discover
# Header: x-nvm-api-key: YOUR_API_KEY
# Query:  ?side=sell  (optional: sell | buy)
#         ?category=DeFi  (optional, case-insensitive)
#
# Response shape for sellers[]:
#   name, teamName, category, description, keywords[],
#   servicesSold, pricing{perRequest, meteringUnit, ...},
#   planIds[], nvmAgentId, endpointUrl, walletAddress
# ────────────────────────────────────────────────────────────

DISCOVERY_URL = "https://nevermined.ai/hackathon/register/api/discover"


async def discover_from_hackathon_api(nvm_api_key: str) -> list[dict]:
    """Hit the hackathon Discovery API for all registered sellers.

    This is the PRIMARY discovery source. Returns structured data
    with planIds[], nvmAgentId, endpointUrl for every seller team.
    """
    results = []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                DISCOVERY_URL,
                params={"side": "sell"},
                headers={"x-nvm-api-key": nvm_api_key},
            )

        if resp.status_code != 200:
            logger.warning(f"Discovery API returned {resp.status_code}")
            return []

        data = resp.json()
        sellers = data.get("sellers", [])
        logger.info(f"Discovery API returned {len(sellers)} sellers")

        for seller in sellers:
            # Skip entries without endpoints — can't probe them
            endpoint_url = seller.get("endpointUrl", "")
            if not endpoint_url:
                continue

            # Use first planId, or empty
            plan_ids = seller.get("planIds", [])
            plan_id = plan_ids[0] if plan_ids else ""

            agent_id = seller.get("nvmAgentId", "")

            # Build pricing dict from the structured pricing field
            raw_pricing = seller.get("pricing", {})
            pricing = {}
            if raw_pricing:
                pricing = {
                    "per_request": raw_pricing.get("perRequest"),
                    "metering_unit": raw_pricing.get("meteringUnit"),
                    "raw": raw_pricing,
                }

            results.append({
                "agent_id": agent_id or endpoint_url,  # fallback ID
                "name": seller.get("name", "Unknown"),
                "description": seller.get("description", ""),
                "url": endpoint_url.rstrip("/"),
                "plan_id": plan_id,
                "tags": seller.get("keywords", []),
                "pricing": pricing,
                "category": seller.get("category", ""),
                "team_name": seller.get("teamName", ""),
            })

    except Exception as e:
        logger.error(f"Discovery API error: {e}", exc_info=True)

    return results


def load_known_agents(config_path: str = "agents_config.json") -> list[dict]:
    """Load hardcoded fallback agent list (for agents not in Discovery API)."""
    p = Path(config_path)
    if not p.exists():
        return []
    with open(p) as f:
        return json.load(f)


async def discover_from_sdk(payments: Payments, agent_ids: list[str]) -> list[dict]:
    """Fetch agent metadata from Nevermined SDK for known IDs.
    Secondary source — used when Discovery API doesn't have full details."""
    results = []
    for agent_id in agent_ids:
        if not agent_id:
            continue
        try:
            agent = payments.agents.get_agent(agent_id)
            if not agent:
                continue
            plans = agent.get("plans", [])
            plan_id = plans[0].get("planId", "") if plans else ""
            results.append({
                "agent_id": agent_id,
                "name": agent.get("name", "Unknown"),
                "description": agent.get("description", ""),
                "url": _extract_url(agent),
                "plan_id": plan_id,
                "tags": agent.get("tags", []),
                "pricing": {},
            })
        except Exception as e:
            logger.warning(f"SDK discovery failed for {agent_id[:20]}: {e}")
    return results


def _extract_url(agent: dict) -> str:
    """Extract the POST endpoint URL from agent metadata."""
    endpoints = agent.get("endpoints", [])
    for ep in endpoints:
        if isinstance(ep, dict):
            for method, url in ep.items():
                if method.upper() == "POST":
                    return url.rsplit("/data", 1)[0] if url.endswith("/data") else url
    return ""


async def probe_pricing(url: str) -> dict:
    """GET /pricing from a seller. Returns tiers dict or empty."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{url}/pricing")
        if resp.status_code == 200:
            data = resp.json()
            return data.get("tiers", {})
    except Exception as e:
        logger.debug(f"No /pricing at {url}: {e}")
    return {}


async def scan_loop(
    sheet: CentralSheet,
    payments: Payments,
    probe_callback,           # async callable(agent_info, sheet, payments)
    interval: int = 300,
    nvm_api_key: str = "",
):
    """Main scanner loop. Runs as asyncio background task.

    Discovery priority:
    1. Hackathon Discovery API (primary — all registered teams)
    2. Nevermined SDK get_agent() (secondary — for enriching with full metadata)
    3. agents_config.json (fallback — manually added agents)
    """
    nvm_api_key = nvm_api_key or os.environ.get("NVM_API_KEY", "")

    while True:
        try:
            # 1. PRIMARY: Hit hackathon Discovery API
            api_agents = await discover_from_hackathon_api(nvm_api_key)

            # 2. FALLBACK: Load manually configured agents
            config_agents = load_known_agents()

            # 3. SECONDARY: Enrich via SDK for any agent_ids we have
            all_agent_ids = [
                a["agent_id"] for a in api_agents + config_agents
                if a.get("agent_id", "").startswith("did:")
            ]
            sdk_agents = await discover_from_sdk(payments, all_agent_ids)

            # 4. Merge all sources (API takes priority)
            all_agents: dict[str, dict] = {}
            # Config first (lowest priority)
            for ca in config_agents:
                aid = ca.get("agent_id") or ca.get("url", "")
                if aid:
                    all_agents[aid] = ca
            # SDK enriches
            for sa in sdk_agents:
                aid = sa["agent_id"]
                if aid in all_agents:
                    all_agents[aid].update(
                        {k: v for k, v in sa.items() if v}
                    )
                else:
                    all_agents[aid] = sa
            # API overwrites (highest priority)
            for aa in api_agents:
                aid = aa["agent_id"]
                if aid in all_agents:
                    all_agents[aid].update(
                        {k: v for k, v in aa.items() if v}
                    )
                else:
                    all_agents[aid] = aa

            # 5. Diff against known
            known_ids = {a["agent_id"] for a in sheet.read_agents()}
            new_agents = [a for aid, a in all_agents.items()
                          if aid not in known_ids and aid]

            # 6. Probe /pricing for new agents (enriches pricing data)
            for agent in new_agents:
                if agent.get("url") and not agent.get("pricing"):
                    agent["pricing"] = await probe_pricing(agent["url"])

            # 7. Write new agents to sheet
            for agent in new_agents:
                sheet.write_agent(
                    agent_id=agent.get("agent_id", agent.get("url", "")),
                    name=agent.get("name", "Unknown"),
                    url=agent.get("url", ""),
                    plan_id=agent.get("plan_id", ""),
                    pricing=agent.get("pricing"),
                    tags=agent.get("tags"),
                    description=agent.get("description", ""),
                    category=agent.get("category", ""),
                    team_name=agent.get("team_name", ""),
                )
                logger.info(
                    f"New agent: {agent.get('name')} "
                    f"[{agent.get('team_name', '?')}] "
                    f"({agent.get('url')})"
                )

            # 8. Spawn probes for new agents
            for agent in new_agents:
                asyncio.create_task(
                    probe_callback(agent, sheet, payments)
                )

            # 9. Re-probe agents needing re-evaluation
            for agent in sheet.read_agents(status="reeval"):
                asyncio.create_task(
                    probe_callback(agent, sheet, payments)
                )

            logger.info(
                f"Scan complete: {len(new_agents)} new, "
                f"{len(known_ids)} known, "
                f"{len(api_agents)} from Discovery API"
            )

        except Exception as e:
            logger.error(f"scan_loop error: {e}", exc_info=True)

        await asyncio.sleep(interval)
```

**`agents_config.json` format** (fallback for agents NOT in the Discovery API):

```json
[
  {
    "agent_id": "did:nv:abc123...",
    "name": "Team Alpha Weather Agent",
    "url": "https://team-alpha.example.com",
    "plan_id": "did:nv:plan456..."
  },
  {
    "agent_id": "",
    "name": "Team Beta (URL only)",
    "url": "http://192.168.1.50:3000",
    "plan_id": ""
  }
]
```

---

## Component 3: Probe Runner (`probe_runner.py`)

**Raw Python. No agent framework. Stateless fire-and-forget.**

Calls the starter kit's `purchase_data_impl` directly. Each probe is an async function with explicit args — clean context isolation.

```python
# probe_runner.py

import asyncio
import time
import logging

from payments_py import Payments

from .central_sheet import CentralSheet
from .buy_impl import purchase_data_impl, build_token_options

logger = logging.getLogger(__name__)


# Default test queries — overridden by eval sub-agents later
DEFAULT_QUERIES = [
    "What services do you provide and at what price?",
    "Give me a sample analysis of current AI market trends",
    "Provide concrete data points on agent marketplace adoption",
]


async def run_probe(
    agent_info: dict,
    sheet: CentralSheet,
    payments: Payments,
    queries: list[str] | None = None,
    eval_callback=None,  # async callable(agent_id, sheet) — triggers evaluation
):
    """Probe an agent with test queries. Stateless, fire-and-forget.

    Args:
        agent_info: dict with agent_id, plan_id, url keys
        sheet: CentralSheet instance
        payments: Payments SDK instance
        queries: Custom test queries (uses DEFAULT_QUERIES if None)
        eval_callback: Called after probes complete to trigger evaluation
    """
    agent_id = agent_info.get("agent_id", agent_info.get("url", ""))
    plan_id = agent_info.get("plan_id", "")
    url = agent_info.get("url", "")

    if not url or not plan_id:
        logger.warning(f"Skip probe for {agent_id}: missing url or plan_id")
        return

    test_queries = queries or DEFAULT_QUERIES
    probe_ids = []

    for query in test_queries:
        t0 = time.perf_counter()
        try:
            # Reuse _impl from starter kit — framework-agnostic, ~50 lines
            result = await asyncio.to_thread(
                purchase_data_impl,
                payments=payments,
                plan_id=plan_id,
                seller_url=url,
                query=query,
                agent_id=agent_id if agent_id.startswith("did:") else None,
            )
            latency = (time.perf_counter() - t0) * 1000

            response_text = result.get("response", "")
            credits_used = result.get("credits_used", 0)
            is_success = result.get("status") == "success"

            probe_id = sheet.write_probe(
                agent_id=agent_id,
                query=query,
                response=response_text if is_success else "",
                credits_spent=credits_used,
                latency_ms=latency,
                response_bytes=len(response_text.encode()) if is_success else 0,
                http_status=200 if is_success else 0,
                error=None if is_success else result["content"][0]["text"],
            )

            # Track spending in ledger
            if credits_used > 0:
                sheet.write_ledger(
                    direction="out",
                    credits=credits_used,
                    purpose="probe",
                    agent_id=agent_id,
                    detail=query[:100],
                )

            if is_success:
                probe_ids.append(probe_id)

            logger.info(
                f"Probe {agent_id[:20]}: "
                f"{'OK' if is_success else 'FAIL'} "
                f"credits={credits_used} latency={latency:.0f}ms"
            )

        except Exception as e:
            latency = (time.perf_counter() - t0) * 1000
            sheet.write_probe(
                agent_id=agent_id, query=query, response="",
                credits_spent=0, latency_ms=latency,
                response_bytes=0, http_status=0, error=str(e),
            )
            logger.error(f"Probe exception for {agent_id[:20]}: {e}")

    # Update status
    if probe_ids:
        sheet.update_agent_status(agent_id, "probed")
        # Trigger evaluation pipeline if callback provided
        if eval_callback:
            asyncio.create_task(eval_callback(agent_id, sheet))
    else:
        sheet.update_agent_status(agent_id, "dead")
```

### `buy_impl.py` — Copied from starter kit

This is a verbatim copy of the starter kit's `_impl` functions. They are framework-agnostic — plain functions with explicit args.

```python
# buy_impl.py
# Copied from agents/buyer-simple-agent/src/tools/

import base64
import json
import httpx
from payments_py import Payments
from payments_py.x402.resolve_scheme import resolve_scheme
from payments_py.x402.types import CardDelegationConfig, X402TokenOptions


# ── Token options (from tools/token_options.py) ─────────────

_SPENDING_LIMIT_CENTS = 10_000  # $100
_DURATION_SECS = 604_800        # 7 days


def build_token_options(payments: Payments, plan_id: str) -> X402TokenOptions:
    """Resolve scheme and build X402TokenOptions."""
    scheme = resolve_scheme(payments, plan_id)
    if scheme != "nvm:card-delegation":
        return X402TokenOptions(scheme=scheme)

    methods = payments.delegation.list_payment_methods()
    if not methods:
        raise ValueError("Fiat plan requires payment method. Add at nevermined.app.")
    pm = methods[0]
    return X402TokenOptions(
        scheme=scheme,
        delegation_config=CardDelegationConfig(
            provider_payment_method_id=pm.id,
            spending_limit_cents=_SPENDING_LIMIT_CENTS,
            duration_secs=_DURATION_SECS,
            currency="usd",
        ),
    )


# ── Purchase (from tools/purchase.py) ──────────────────────

def _error(message: str) -> dict:
    return {"status": "error", "content": [{"text": message}], "credits_used": 0}


def purchase_data_impl(
    payments: Payments,
    plan_id: str,
    seller_url: str,
    query: str,
    agent_id: str | None = None,
) -> dict:
    """Purchase data from a seller using x402 protocol.

    Returns dict with: status, content, response, credits_used
    """
    try:
        token_options = build_token_options(payments, plan_id)
        token_result = payments.x402.get_x402_access_token(
            plan_id=plan_id,
            agent_id=agent_id,
            token_options=token_options,
        )
        access_token = token_result.get("accessToken")
        if not access_token:
            return _error("Failed to generate x402 access token.")

        with httpx.Client(timeout=60.0) as client:
            response = client.post(
                f"{seller_url}/data",
                headers={
                    "Content-Type": "application/json",
                    "payment-signature": access_token,
                },
                json={"query": query},
            )

        if response.status_code == 402:
            details = ""
            pr_header = response.headers.get("payment-required", "")
            if pr_header:
                try:
                    decoded = json.loads(base64.b64decode(pr_header).decode("utf-8"))
                    details = f"\nPayment details: {json.dumps(decoded, indent=2)}"
                except Exception:
                    pass
            return {
                "status": "payment_required",
                "content": [{"text": f"Payment required (HTTP 402).{details}"}],
                "credits_used": 0,
            }

        if response.status_code != 200:
            return _error(f"HTTP {response.status_code}: {response.text[:500]}")

        data = response.json()
        return {
            "status": "success",
            "content": [{"text": data.get("response", "")}],
            "response": data.get("response", ""),
            "credits_used": data.get("credits_used", 0),
        }

    except httpx.ConnectError:
        return _error(f"Cannot connect to {seller_url}.")
    except Exception as e:
        return _error(f"Purchase failed: {e}")


# ── Balance (from tools/balance.py) ─────────────────────────

def check_balance_impl(payments: Payments, plan_id: str) -> dict:
    """Check credit balance for a plan."""
    try:
        result = payments.plans.get_plan_balance(plan_id)
        return {
            "status": "success",
            "balance": result.balance,
            "is_subscriber": result.is_subscriber,
        }
    except Exception as e:
        return {"status": "error", "balance": 0, "is_subscriber": False}


# ── Discover pricing (from tools/discover.py) ───────────────

def discover_pricing_impl(seller_url: str) -> dict:
    """GET /pricing from seller."""
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(f"{seller_url}/pricing")
        if response.status_code != 200:
            return {"status": "error", "tiers": {}}
        data = response.json()
        return {
            "status": "success",
            "plan_id": data.get("planId", ""),
            "tiers": data.get("tiers", {}),
        }
    except Exception:
        return {"status": "error", "tiers": {}}
```

---

## Component 4: Evaluation Pipeline (`evaluation.py`)

**This is the extensibility layer. Each scorer sub-agent is a function that reads probes, runs analysis, and writes to the `evaluations` table.**

The pipeline dispatches to registered evaluators. Adding a new scorer = registering a new function. No existing code changes.

```python
# evaluation.py

import asyncio
import logging
from typing import Protocol, Callable, Any

from .central_sheet import CentralSheet

logger = logging.getLogger(__name__)


class Evaluator(Protocol):
    """Protocol for scorer sub-agents.

    Each evaluator:
    1. Reads probes (and optionally other evaluations) from the sheet
    2. Produces metrics (any JSON-serializable dict)
    3. Writes results to the evaluations table via sheet.write_evaluation()
    """
    async def __call__(
        self,
        agent_id: str,
        sheet: CentralSheet,
        **kwargs: Any,
    ) -> None: ...


class EvaluationPipeline:
    """Registry of scorer sub-agents. Dispatches evaluation for an agent."""

    def __init__(self):
        self._evaluators: dict[str, Evaluator] = {}

    def register(self, name: str, evaluator: Evaluator) -> None:
        """Register a scorer sub-agent.

        Args:
            name: Unique evaluator name (written to evaluations.evaluator column)
            evaluator: Async callable matching the Evaluator protocol
        """
        self._evaluators[name] = evaluator
        logger.info(f"Registered evaluator: {name}")

    def unregister(self, name: str) -> None:
        self._evaluators.pop(name, None)

    @property
    def evaluator_names(self) -> list[str]:
        return list(self._evaluators.keys())

    async def run(self, agent_id: str, sheet: CentralSheet, **kwargs) -> None:
        """Run all registered evaluators for an agent.

        Evaluators run concurrently. Each writes its own rows to the
        evaluations table. Failures in one evaluator don't block others.
        """
        # Pre-check: only evaluate if there are successful probes
        probes = sheet.read_probes(agent_id=agent_id)
        successful = [p for p in probes if p["error"] is None]
        if not successful:
            logger.info(f"No successful probes for {agent_id}, skipping eval")
            sheet.update_agent_status(agent_id, "dead")
            return

        tasks = []
        for name, evaluator in self._evaluators.items():
            tasks.append(
                self._run_one(name, evaluator, agent_id, sheet, **kwargs)
            )

        await asyncio.gather(*tasks)
        sheet.update_agent_status(agent_id, "evaluated")
        logger.info(f"Evaluation complete for {agent_id}: "
                     f"{len(tasks)} evaluators ran")

    async def _run_one(
        self, name: str, evaluator: Evaluator,
        agent_id: str, sheet: CentralSheet, **kwargs
    ) -> None:
        try:
            await evaluator(agent_id, sheet, **kwargs)
        except Exception as e:
            logger.error(f"Evaluator '{name}' failed for {agent_id}: {e}",
                         exc_info=True)


# ── Global pipeline instance ────────────────────────────────

pipeline = EvaluationPipeline()
```

### Example evaluator: pass/fail gate (built-in, no LLM)

```python
# evaluators/gate.py

from ..central_sheet import CentralSheet


async def gate_evaluator(agent_id: str, sheet: CentralSheet, **kwargs) -> None:
    """Binary pass/fail gate. Marks agents with >50% error rate as dead.
    No LLM needed — pure data check.
    """
    probes = sheet.read_probes(agent_id=agent_id)
    if not probes:
        return

    total = len(probes)
    errors = sum(1 for p in probes if p["error"] is not None)
    success_rate = (total - errors) / total

    sheet.write_evaluation(
        agent_id=agent_id,
        evaluator="gate",
        metrics={
            "total_probes": total,
            "errors": errors,
            "success_rate": round(success_rate, 3),
            "passed": success_rate > 0.5,
        },
        summary=f"{'PASS' if success_rate > 0.5 else 'FAIL'}: "
                f"{total - errors}/{total} probes succeeded",
    )

    if success_rate <= 0.5:
        sheet.update_agent_status(agent_id, "dead")
```

### Example evaluator: placeholder for your quality scorer (LLM-based)

This is where your sub-agent logic goes. The structure is identical — read probes, do work, write evaluation.

```python
# evaluators/quality_judge.py
# PLACEHOLDER — you finalize the scoring logic

import asyncio
from strands import Agent, tool
from strands.models import BedrockModel

from ..central_sheet import CentralSheet


def create_quality_judge(model: BedrockModel) -> callable:
    """Factory: creates a quality_judge evaluator bound to a model.

    Returns an async callable matching the Evaluator protocol.
    """

    @tool
    def read_probes(agent_id: str) -> dict:
        """Read all successful probe results for an agent."""
        # Injected at call time via closure
        return {"probes": _sheet.read_probes(agent_id=agent_id)}

    @tool
    def write_evaluation(agent_id: str, metrics_json: str,
                         summary: str) -> dict:
        """Write evaluation results for an agent."""
        import json
        metrics = json.loads(metrics_json)
        _sheet.write_evaluation(
            agent_id=agent_id,
            evaluator="quality_judge",
            metrics=metrics,
            summary=summary,
        )
        return {"status": "ok"}

    scorer = Agent(
        model=model,
        tools=[read_probes, write_evaluation],
        system_prompt="""You are a quality assessment agent.
You evaluate agents based on their actual responses to test queries.

When asked to evaluate an agent:
1. Call read_probes to get all probe results.
2. For each successful probe, assess the response quality.
3. Produce your metrics as a JSON object — you decide the schema.
4. Call write_evaluation with your metrics JSON and a human-readable summary.

Your metrics MUST include a 'roi' field: quality_score / credits_spent.
Beyond that, the metric schema is yours to define.""",
    )

    # Closure variable for tools to access
    _sheet = None

    async def quality_judge_evaluator(
        agent_id: str, sheet: CentralSheet, **kwargs
    ) -> None:
        nonlocal _sheet
        _sheet = sheet
        await asyncio.to_thread(
            scorer, f"Evaluate agent {agent_id}. Read its probes and score them."
        )

    return quality_judge_evaluator
```

### Registering evaluators at startup

```python
# In main.py startup:

from .evaluation import pipeline
from .evaluators.gate import gate_evaluator
from .evaluators.quality_judge import create_quality_judge

# Always-on: no-LLM gate
pipeline.register("gate", gate_evaluator)

# LLM-based quality judge
pipeline.register("quality_judge", create_quality_judge(model))

# Future: you add more here. Each is one line.
# pipeline.register("comparative", create_comparative_scorer(model))
# pipeline.register("test_query_gen", create_query_generator(model))
```

---

## Component 5: Consulting Agent (`consulting_agent.py`)

**Strands agent with `@requires_payment`. The revenue generator.**

```python
# consulting_agent.py

import asyncio
import json
import os
import logging

from strands import Agent, tool
from strands.models import BedrockModel
from payments_py import Payments
from payments_py.x402.strands import requires_payment

from .central_sheet import CentralSheet
from .buy_impl import purchase_data_impl

logger = logging.getLogger(__name__)


def create_consulting_agent(
    model: BedrockModel,
    sheet: CentralSheet,
    payments: Payments,
    plan_id: str,
    agent_id: str | None = None,
) -> Agent:
    """Create the consulting agent with payment-protected tools.

    Args:
        model: BedrockModel instance
        sheet: CentralSheet instance
        payments: Payments SDK instance (builder key for seller side)
        plan_id: Our registered plan ID
        agent_id: Our registered agent ID
    """

    # ── Internal tools (no payment needed) ──────────────────

    @tool
    def read_portfolio() -> dict:
        """Read the current portfolio: ranked agents with quality data,
        cost info, and probe counts."""
        portfolio = sheet.read_portfolio()
        pnl = sheet.get_pnl()
        return {
            "portfolio": portfolio,
            "pnl": pnl,
            "agent_count": len(portfolio),
        }

    @tool
    def get_agent_report(target_agent_id: str) -> dict:
        """Get detailed assessment of a specific agent: all probes,
        all evaluations, historical performance."""
        probes = sheet.read_probes(agent_id=target_agent_id, limit=20)
        evals = sheet.read_evaluations(agent_id=target_agent_id, limit=20)
        agents = sheet.read_agents()
        agent_info = next(
            (a for a in agents if a["agent_id"] == target_agent_id), None
        )
        return {
            "agent": agent_info,
            "probes": probes,
            "evaluations": evals,
        }

    @tool
    def buy_from_agent(target_agent_id: str, query: str) -> dict:
        """Buy data from a specific agent using x402.
        Logs the purchase to the ledger."""
        agents = sheet.read_agents()
        agent_info = next(
            (a for a in agents if a["agent_id"] == target_agent_id), None
        )
        if not agent_info:
            return {"status": "error", "content": [{"text": f"Agent {target_agent_id} not found"}]}

        result = purchase_data_impl(
            payments=payments,
            plan_id=agent_info["plan_id"],
            seller_url=agent_info["url"],
            query=query,
            agent_id=target_agent_id if target_agent_id.startswith("did:") else None,
        )

        credits_used = result.get("credits_used", 0)
        if result.get("status") == "success" and credits_used > 0:
            sheet.write_ledger(
                direction="out",
                credits=credits_used,
                purpose="consulting_upstream",
                agent_id=target_agent_id,
                detail=query[:100],
            )

        return result

    # ── Payment-protected entry point ───────────────────────
    # Accept BOTH plan IDs so buyers on either rail can pay
    plan_ids = [pid for pid in [plan_id] if pid]
    # Note: if you have a fiat plan_id too, pass both:
    # plan_ids = [pid for pid in [usdc_plan_id, fiat_plan_id] if pid]

    @tool(context=True)
    @requires_payment(
        payments=payments,
        plan_id=plan_id,          # Primary plan
        credits=1,
        agent_id=agent_id,
    )
    def consulting_query(query: str, tool_context=None) -> dict:
        """Process a consulting request. This is the billable entry point.
        After this tool runs, the agent will use read_portfolio,
        buy_from_agent, and get_agent_report to fulfill the request.

        Args:
            query: The client's consulting query.
        """
        # The payment decorator handles verify/settle automatically.
        # This tool just validates the query and returns it for the agent to process.
        return {
            "status": "accepted",
            "content": [{"text": f"Query accepted: {query}"}],
            "query": query,
        }

    SYSTEM_PROMPT = """\
You are a consulting agent in an AI agent marketplace. Clients pay you to either:

(a) Provide intelligence about the marketplace — which agents are good,
    which are overpriced, quality rankings, cost comparisons.

(b) Fulfill data requests by buying from the best available agents on
    the client's behalf and delivering a synthesized product.

Your workflow:
1. First call consulting_query with the client's request (this handles payment).
2. Call read_portfolio to see all evaluated agents.
3. For intelligence requests (a): analyze the portfolio and respond.
4. For data requests (b): identify the top 2-3 agents for the task,
   call buy_from_agent for each, synthesize their outputs, and include
   a cost breakdown.

Always include in your response:
- Which agents you consulted and why
- Quality data from your evaluation engine
- Cost breakdown (client paid X, upstream cost Y)
- Confidence level
"""

    return Agent(
        model=model,
        tools=[consulting_query, read_portfolio, get_agent_report, buy_from_agent],
        system_prompt=SYSTEM_PROMPT,
    )
```

**Key patterns from the actual starter kit code:**

1. `@tool(context=True)` + `@requires_payment(...)` + `tool_context=None` — this exact decorator stack is how the seller agent does it
2. Only `consulting_query` gets `@requires_payment` — the other tools are internal
3. The `invocation_state={"payment_token": token}` is passed from the FastAPI layer (see web server below)
4. After the agent runs, `state.get("payment_settlement")` contains the settlement receipt — the decorator injects it

---

## Component 6: Web Server (`main.py`)

**Single FastAPI process. Hosts everything.**

```python
# main.py

import asyncio
import base64
import json
import os
import logging

from dotenv import load_dotenv
load_dotenv()

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from strands.models import BedrockModel

from payments_py import Payments, PaymentOptions
from payments_py.common.types import PlanMetadata
from payments_py.x402.strands import extract_payment_required
from payments_py.plans import (
    get_erc20_price_config,
    get_fixed_credits_config,
    get_fiat_price_config,
)

from .central_sheet import CentralSheet
from .scanner import scan_loop
from .probe_runner import run_probe
from .evaluation import pipeline
from .evaluators.gate import gate_evaluator
from .consulting_agent import create_consulting_agent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ──────────────────────────────────────────────────

NVM_API_KEY = os.environ["NVM_API_KEY"]
NVM_ENVIRONMENT = os.getenv("NVM_ENVIRONMENT", "sandbox")
PORT = int(os.getenv("PORT", "3000"))
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "300"))

# ── Shared resources ────────────────────────────────────────

sheet = CentralSheet("portfolio.db")

payments = Payments.get_instance(
    PaymentOptions(nvm_api_key=NVM_API_KEY, environment=NVM_ENVIRONMENT)
)

model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-6",
    streaming=True,
)

# ── Agent registration ──────────────────────────────────────

USDC_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # Base Sepolia
OUR_HOST = os.getenv("OUR_HOST", f"http://localhost:{PORT}")

# Registration outputs — set via env or auto-registered at startup
OUR_AGENT_ID = os.getenv("NVM_AGENT_ID", "")
OUR_PLAN_ID_USDC = os.getenv("NVM_PLAN_ID_USDC", "")     # Crypto plan
OUR_PLAN_ID_FIAT = os.getenv("NVM_PLAN_ID_FIAT", "")     # Fiat/Stripe plan
# For x402, use whichever plan buyers subscribe to — both work
OUR_PLAN_ID = os.getenv("NVM_PLAN_ID", "")                # Primary (used by @requires_payment)


def register_if_needed():
    """Register our agent + DUAL payment plans (USDC + Fiat) with Nevermined.

    Hackathon tip: create both rails so every buyer can pay,
    whether they have crypto wallets or just credit cards.
    Stripe test card: 4242 4242 4242 4242, any future expiry, any CVC.
    """
    global OUR_AGENT_ID, OUR_PLAN_ID, OUR_PLAN_ID_USDC, OUR_PLAN_ID_FIAT
    if OUR_AGENT_ID and OUR_PLAN_ID:
        return

    logger.info("Registering agent and DUAL payment plans with Nevermined...")

    # ── 1. Register agent + USDC plan (crypto rail) ─────────
    result = payments.agents.register_agent_and_plan(
        agent_metadata={
            "name": "Portfolio Manager — Agent Rating & Consulting",
            "description": (
                "Evaluates every agent in the marketplace. "
                "Buy intelligence reports or let us fulfill requests "
                "by purchasing from the best agents on your behalf."
            ),
            "tags": ["consulting", "ratings", "portfolio", "meta-agent"],
        },
        agent_api={
            "endpoints": [{"POST": f"{OUR_HOST}/data"}],
        },
        plan_metadata={
            "name": "Consulting Credits (USDC)",
            "description": "1 credit per consulting query — pay with USDC",
        },
        price_config=get_erc20_price_config(
            10_000_000,  # 10 USDC (6 decimals)
            USDC_ADDRESS,
            payments.account_address,
        ),
        credits_config=get_fixed_credits_config(100, 1),  # 100 credits, 1 per request
        access_limit="credits",
    )
    OUR_AGENT_ID = result["agentId"]
    OUR_PLAN_ID_USDC = result["planId"]
    OUR_PLAN_ID = OUR_PLAN_ID_USDC  # Primary plan for @requires_payment
    logger.info(f"Registered USDC plan: agent={OUR_AGENT_ID} plan={OUR_PLAN_ID_USDC}")

    # ── 2. Register fiat/Stripe plan (credit card rail) ─────
    try:
        fiat_plan = payments.plans.register_credits_plan(
            plan_metadata=PlanMetadata(
                name="Consulting Credits (Card)",
                description="1 credit per consulting query — pay with credit card",
            ),
            price_config=get_fiat_price_config(
                999,                          # $9.99 in cents
                payments.account_address,
            ),
            credits_config=get_fixed_credits_config(100, 1),
        )
        OUR_PLAN_ID_FIAT = fiat_plan.get("planId", "")
        logger.info(f"Registered fiat plan: {OUR_PLAN_ID_FIAT}")
    except Exception as e:
        logger.warning(f"Fiat plan registration failed (non-fatal): {e}")
        # USDC plan still works — fiat is a bonus


# ── Evaluation pipeline setup ───────────────────────────────

pipeline.register("gate", gate_evaluator)
# Add your scorer sub-agents here:
# from .evaluators.quality_judge import create_quality_judge
# pipeline.register("quality_judge", create_quality_judge(model))


async def eval_callback(agent_id: str, sheet: CentralSheet):
    """Triggered after probes complete. Runs all registered evaluators."""
    await pipeline.run(agent_id, sheet)


# ── Consulting agent ────────────────────────────────────────

# Serialize concurrent requests (Strands Agent is not thread-safe)
agent_lock = asyncio.Lock()


# ── FastAPI app ─────────────────────────────────────────────

app = FastAPI(title="Portfolio Manager Agent")


class DataRequest(BaseModel):
    query: str


@app.on_event("startup")
async def startup():
    register_if_needed()

    # Create consulting agent (needs plan_id from registration)
    global consulting_agent
    consulting_agent = create_consulting_agent(
        model=model,
        sheet=sheet,
        payments=payments,
        plan_id=OUR_PLAN_ID,
        agent_id=OUR_AGENT_ID,
    )

    # Start scanner background task
    asyncio.create_task(
        scan_loop(
            sheet=sheet,
            payments=payments,
            probe_callback=lambda a, s, p: run_probe(
                a, s, p, eval_callback=eval_callback
            ),
            interval=SCAN_INTERVAL,
            nvm_api_key=NVM_API_KEY,
        )
    )
    logger.info(f"Scanner started (interval={SCAN_INTERVAL}s)")


@app.post("/data")
async def data_endpoint(request: Request, body: DataRequest):
    """Client-facing consulting endpoint. x402 payment required.

    Pattern matches seller-simple-agent/src/agent.py exactly:
    1. Extract payment-signature header
    2. Pass as invocation_state
    3. Run agent
    4. Check for payment_required / settlement
    """
    try:
        payment_token = request.headers.get("payment-signature", "")
        state = {"payment_token": payment_token} if payment_token else {}

        async with agent_lock:
            result = await asyncio.to_thread(
                consulting_agent, body.query, invocation_state=state
            )

        # Check if payment was required but not fulfilled
        payment_required = extract_payment_required(consulting_agent.messages)
        if payment_required and not state.get("payment_settlement"):
            encoded = base64.b64encode(
                json.dumps(payment_required).encode()
            ).decode()
            return JSONResponse(
                status_code=402,
                content={
                    "error": "Payment Required",
                    "message": str(result),
                },
                headers={"payment-required": encoded},
            )

        # Success — record revenue
        settlement = state.get("payment_settlement")
        credits = int(settlement.credits_redeemed) if settlement else 0
        if credits > 0:
            sheet.write_ledger(
                direction="in",
                credits=credits,
                purpose="consulting_revenue",
                detail=body.query[:100],
            )

        return JSONResponse(content={
            "response": str(result),
            "credits_used": credits,
        })

    except Exception as e:
        logger.error(f"Error in /data: {e}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
        )


@app.get("/pricing")
async def pricing():
    """Pricing info (unprotected). Exposes BOTH payment rails."""
    plans = {"usdc": OUR_PLAN_ID_USDC}
    if OUR_PLAN_ID_FIAT:
        plans["fiat"] = OUR_PLAN_ID_FIAT
    return JSONResponse(content={
        "planId": OUR_PLAN_ID,         # Primary plan (for x402 buyers)
        "plans": plans,                 # Both rails
        "tiers": {
            "consulting": {
                "credits": 1,
                "description": "Intelligence query or upstream fulfillment",
            },
        },
        "payment_rails": {
            "usdc": "Pay with USDC on Base Sepolia",
            "fiat": "Pay with credit card via Stripe (test: 4242 4242 4242 4242)",
        },
    })


@app.get("/portfolio")
async def portfolio_view():
    """Public dashboard — ranked agents, evaluation data, P&L."""
    return JSONResponse(content={
        "portfolio": sheet.read_portfolio(),
        "pnl": sheet.get_pnl(),
        "evaluators": pipeline.evaluator_names,
    })


@app.get("/health")
async def health():
    agents = sheet.read_agents()
    return JSONResponse(content={
        "status": "ok",
        "agents_tracked": len(agents),
        "agents_evaluated": len([a for a in agents if a["status"] == "evaluated"]),
        "total_probes": len(sheet.read_probes(limit=99999)),
        "pnl": sheet.get_pnl(),
    })


def main():
    logger.info(f"Portfolio Manager on http://localhost:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
```

---

## `pyproject.toml`

```toml
[tool.poetry]
name = "portfolio-manager-agent"
version = "0.1.0"
description = "Autonomous agent that evaluates and trades in the AI agent marketplace"
package-mode = false

[tool.poetry.dependencies]
python = "^3.10"
# Strands SDK — need openai extra for fallback, a2a for agent discovery
strands-agents = {version = ">=1.0.0", extras = ["openai", "a2a"]}
# Payments SDK — strands for @requires_payment, a2a for discovery
payments-py = {version = ">=1.3.3", extras = ["strands", "a2a", "langchain"]}
# HTTP
httpx = "^0.28.0"
fastapi = "^0.120.0"
uvicorn = ">=0.34.2,<1.0.0"
# AWS
boto3 = ">=1.35.0"
# Util
python-dotenv = "^1.0.0"

[tool.poetry.extras]
agentcore = ["bedrock-agentcore"]

[tool.poetry.scripts]
server = "src.main:main"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"
```

---

## `.env.example`

```bash
# ── Nevermined ──────────────────────────────────────────────
NVM_API_KEY=nvm:...
NVM_AGENT_ID=
NVM_PLAN_ID=
NVM_PLAN_ID_USDC=
NVM_PLAN_ID_FIAT=
NVM_ENVIRONMENT=sandbox

# ── AWS / Bedrock ───────────────────────────────────────────
# If using EC2 instance profile, these are NOT needed:
# AWS_ACCESS_KEY_ID=
# AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1

# ── App ─────────────────────────────────────────────────────
PORT=3000
OUR_HOST=http://<your-ec2-public-ip>:3000
SCAN_INTERVAL=300
```

---

## Deployment

### Quick-start: EC2

```bash
# 1. Launch t3.medium, Ubuntu 24.04, open ports 3000 + 22
# 2. SSH in
ssh -i key.pem ubuntu@<ip>

# 3. Install deps
sudo apt update && sudo apt install -y python3.12 python3.12-venv pipx
pipx install poetry

# 4. Clone & install
git clone <your-repo>
cd portfolio-manager-agent
poetry install

# 5. Configure
cp .env.example .env
# Fill in: NVM_API_KEY, AWS creds (for Bedrock), OUR_HOST=http://<public-ip>:3000

# 6. Run
tmux new -s agent
poetry run server
# Ctrl-B D to detach
```

**That's it.** SQLite file lives at `./portfolio.db` on the instance. Scanner runs as an asyncio background task inside the same process — no separate worker, no queue, no cron.

### IAM policy for Bedrock (minimal)

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:InvokeModel",
    "bedrock:InvokeModelWithResponseStream",
    "bedrock:ListInferenceProfiles"
  ],
  "Resource": "arn:aws:bedrock:us-east-1:*:inference-profile/us.anthropic.claude-sonnet-4-6"
}
```

Attach to the EC2 instance role. No `AWS_ACCESS_KEY_ID` in `.env` needed if you use an instance profile.

### When to upgrade

| Signal | Move to |
|---|---|
| Need HTTPS / custom domain | Put an ALB in front, or use Caddy as reverse proxy on the same box |
| Need >1 instance | ECS Fargate + RDS Postgres (swap SQLite for `asyncpg`) |
| Want zero-ops | AgentCore (`.bedrock_agentcore.yaml` already in repo pattern) |

---

## File Structure

```
portfolio-manager-agent/
├── pyproject.toml
├── .env.example
├── agents_config.json          # Hardcoded agent registry from hackathon sheet
├── src/
│   ├── __init__.py
│   ├── main.py                 # FastAPI server, startup, endpoints
│   ├── central_sheet.py        # SQLite wrapper, all tables + views
│   ├── scanner.py              # Background discovery loop
│   ├── probe_runner.py         # Stateless fire-and-forget probes
│   ├── buy_impl.py             # Copied _impl functions from starter kit
│   ├── evaluation.py           # EvaluationPipeline registry + dispatcher
│   ├── consulting_agent.py     # Strands agent with @requires_payment
│   └── evaluators/
│       ├── __init__.py
│       ├── gate.py             # Pass/fail binary gate (no LLM)
│       └── quality_judge.py    # Placeholder: your scorer sub-agents go here
└── .bedrock_agentcore.yaml     # AgentCore deployment config
```

---

## Implementation Order

### Phase 1: Plumbing (must work end-to-end)

1. `central_sheet.py` — run, verify tables create, test CRUD
2. `buy_impl.py` — copy from starter kit, test against local seller-simple-agent
3. `probe_runner.py` — test: probe local seller, verify rows in probes table
4. `evaluation.py` + `evaluators/gate.py` — test: gate runs after probes
5. `scanner.py` — test with `agents_config.json` pointing at local seller
6. `main.py` — minimal: `/health`, `/portfolio`, scanner background task

**Milestone: scanner discovers seller → probes it → gate evaluator runs → portfolio shows data**

### Phase 2: Sell

7. Agent registration with Nevermined (`register_if_needed()`)
8. `consulting_agent.py` — test: can read portfolio and answer questions
9. Wire `@requires_payment` on consulting endpoint
10. `/data` and `/pricing` endpoints live

**Milestone: another team can buy consulting from us via x402**

### Phase 3: Score (your sub-agents)

11. Add your scorer sub-agents to `evaluators/`
12. Register them in `main.py` startup
13. Each writes to `evaluations` table with its own `evaluator` name
14. Portfolio logic reads evaluations to rank agents

**Milestone: portfolio has real quality + ROI rankings**

### Phase 4: Polish

15. Fan-out consulting: buy from top 2-3 agents, synthesize
16. Dashboard endpoint with richer portfolio data
17. AgentCore deployment config

---

## Key Syntax Patterns (verified against actual repo code)

### Buyer-side x402 purchase (from `tools/purchase.py`)

```python
token_result = payments.x402.get_x402_access_token(
    plan_id=plan_id,
    agent_id=agent_id,          # optional, for scoping
    token_options=token_options, # from build_token_options()
)
access_token = token_result.get("accessToken")

response = httpx.post(
    f"{seller_url}/data",
    headers={"payment-signature": access_token},
    json={"query": query},
)
```

### Seller-side payment enforcement (from `strands_agent.py`)

```python
@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=1,
    agent_id=NVM_AGENT_ID,   # required for multi-agent plans
)
def my_tool(query: str, tool_context=None) -> dict:
    # Decorator handles verify + settle automatically
    return {"status": "success", "content": [{"text": "..."}]}
```

### FastAPI endpoint passing payment token (from `agent.py`)

```python
@app.post("/data")
async def data(request: Request, body: DataRequest):
    payment_token = request.headers.get("payment-signature", "")
    state = {"payment_token": payment_token} if payment_token else {}

    result = agent(body.query, invocation_state=state)

    # Check 402
    payment_required = extract_payment_required(agent.messages)
    if payment_required and not state.get("payment_settlement"):
        encoded = base64.b64encode(
            json.dumps(payment_required).encode()
        ).decode()
        return JSONResponse(status_code=402, ...)

    # Read settlement
    settlement = state.get("payment_settlement")
    credits = int(settlement.credits_redeemed) if settlement else 0
```

### Agent + plan registration (from Nevermined docs)

```python
result = payments.agents.register_agent_and_plan(
    agent_metadata={"name": "...", "description": "...", "tags": [...]},
    agent_api={"endpoints": [{"POST": "https://host/data"}]},
    plan_metadata={"name": "...", "description": "..."},
    price_config=get_erc20_price_config(10_000_000, USDC_ADDRESS, payments.account_address),
    credits_config=get_fixed_credits_config(100, 1),  # 100 credits, 1 per request
    access_limit="credits",
)
OUR_AGENT_ID = result["agentId"]
OUR_PLAN_ID = result["planId"]
```

### BedrockModel instantiation (from Strands docs)

```python
from strands.models import BedrockModel

model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-6",
    streaming=True,
)
agent = Agent(model=model, tools=[...], system_prompt="...")
```

### Strands Agent invocation patterns

```python
# Synchronous
result = agent("query")
result = agent("query", invocation_state={"payment_token": token})

# Async streaming (for SSE endpoints)
async for event in agent.stream_async("query"):
    if "data" in event:
        chunk = event["data"]
```

### Hackathon Discovery API

```python
# GET all registered sellers from the hackathon marketplace
import httpx

async with httpx.AsyncClient(timeout=30.0) as client:
    resp = await client.get(
        "https://nevermined.ai/hackathon/register/api/discover",
        params={"side": "sell"},             # or "buy", or omit for both
        headers={"x-nvm-api-key": NVM_API_KEY},
    )

data = resp.json()
# data["sellers"] → [{name, teamName, category, description, keywords[],
#   servicesSold, pricing{perRequest, meteringUnit}, planIds[],
#   nvmAgentId, endpointUrl, walletAddress}, ...]
# data["buyers"]  → [{name, teamName, category, description, keywords[],
#   interests, walletAddress}, ...]
# data["meta"]    → {total, timestamp, filters}
```

### Dual payment plans: USDC + Fiat (from Nevermined docs)

```python
from payments_py.plans import (
    get_erc20_price_config,
    get_fiat_price_config,
    get_fixed_credits_config,
)
from payments_py.common.types import PlanMetadata

USDC_ADDRESS = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # Base Sepolia

# USDC plan (crypto rail)
usdc_price = get_erc20_price_config(
    10_000_000,  # 10 USDC (6 decimals)
    USDC_ADDRESS,
    payments.account_address,
)

# Fiat plan (Stripe rail)
fiat_price = get_fiat_price_config(
    999,                          # $9.99 in cents
    payments.account_address,
)

# Same credits config for both
credits = get_fixed_credits_config(100, 1)  # 100 credits, 1 per request

# Register USDC plan (via register_agent_and_plan for first plan)
result = payments.agents.register_agent_and_plan(
    agent_metadata={...}, agent_api={...},
    plan_metadata={"name": "Plan (USDC)", ...},
    price_config=usdc_price,
    credits_config=credits,
    access_limit="credits",
)

# Register fiat plan separately (agent already exists)
fiat_plan = payments.plans.register_credits_plan(
    plan_metadata=PlanMetadata(name="Plan (Card)"),
    price_config=fiat_price,
    credits_config=credits,
)
# Stripe test card: 4242 4242 4242 4242, any future expiry, any CVC
```

---

## Extensibility Points

| What | How | Where |
|---|---|---|
| New scorer sub-agent | Write async function matching `Evaluator` protocol, call `pipeline.register()` | `evaluators/` dir + `main.py` startup |
| New evaluation metrics | Write any JSON to `evaluations.metrics` column | Your evaluator function |
| New probe query strategy | Pass custom `queries` list to `run_probe()` | Scanner or evaluator |
| New discovery source | Add to `scan_loop()` agent collection; or use Discovery API filters (`?category=DeFi`) | `scanner.py` |
| New consulting tool | Add `@tool` to consulting agent | `consulting_agent.py` |
| A2A seller discovery | Fetch `/.well-known/agent.json`, parse `urn:nevermined:payment` extension | Already in `discover_a2a.py` pattern |
| New pricing tier | Add to `/pricing` response | `main.py` |
| New payment rail | `register_credits_plan()` with different `price_config`; use `plan_ids=[...]` in `@requires_payment` | `main.py` registration |
