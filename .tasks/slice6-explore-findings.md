# Slice 6 Exploration Findings

## 1. Current State of src/main.py

**File**: `/Users/tk/Desktop/nevermind-agent/src/main.py` (74 lines)

**Endpoints**:
- `GET /health` → returns `{"status": "ok"}`
- `GET /portfolio` → returns `sheet.read_portfolio()` (aggregated agent data)

**Global Instances**:
- `sheet = CentralSheet("portfolio.db")` — SQLite-backed portfolio database
- `payments = Payments(PaymentOptions(...))` — Nevermined payments SDK

**Startup Flow**:
- Registers "gate" evaluator via `pipeline.register("gate", gate_evaluator)`
- Spawns background task: `scan_loop(sheet, payments, run_probe, ..., eval_callback=eval_callback)`
- `eval_callback` triggers `pipeline.run(agent_id, sheet)` after probes complete

**Key Pattern**: Evaluation pipeline is pluggable — evaluators are registered and run concurrently after probes succeed.

---

## 2. Current State of src/smoke/

**Directory**: `/Users/tk/Desktop/nevermind-agent/src/smoke/`

### seller.py (199 lines)
- **Purpose**: Smoke test FastAPI seller with x402 payment integration
- **Endpoints**:
  - `GET /health` → `{"status": "ok", "service": "smoke-seller"}`
  - `GET /pricing` → returns `{"planId": PLAN_ID, "agentId": AGENT_ID, "tiers": PRICING}`
  - `POST /data` → requires x402 payment header; calls `process_consulting_query(query)` → returns hardcoded response
- **Payment Flow**: verify → execute → settle (manual pattern from Nevermined docs)
- **Auto-Registration**: `register_if_needed()` on startup registers agent + plan if not in .env
- **Recent Change**: Added `"agentDefinitionUrl": f"{OUR_HOST}/openapi.json"` to agent_api metadata

### buyer.py (107 lines)
- **Purpose**: Scripted x402 buyer for smoke testing (no LLM)
- **Flow**:
  1. `discover_pricing_impl(SELLER_URL)` → get plan_id, agent_id, tiers
  2. `check_balance_impl(payments, plan_id)` → check credits
  3. `purchase_data_impl(payments, plan_id, SELLER_URL, query, agent_id)` → buy data
  4. Print result
- **Entry Point**: `main()` script

### pricing.py (8 lines)
```python
PRICING = {
    "consulting": {
        "credits": 1,
        "description": "Test consulting query"
    }
}
```

---

## 3. Current State of src/central_sheet.py

**File**: `/Users/tk/Desktop/nevermind-agent/src/central_sheet.py` (316 lines)

**Database Schema** (SQLite with WAL mode):
- `agents` — discovered agents (agent_id, name, url, plan_id, pricing, status: new|probed|evaluated|dead)
- `probes` — raw purchase results (agent_id, query, response, credits_spent, latency_ms, error, timestamp)
- `evaluations` — sub-agent results (agent_id, probe_id, evaluator, metrics JSON, summary, timestamp)
- `ledger` — credit tracking (direction: in|out, purpose: probe|consulting_upstream|consulting_revenue, credits, timestamp)
- `portfolio` VIEW — aggregated stats (probe_count, avg_cost, avg_latency, total_spent, eval_count, last_probe, last_eval)

**Key Methods**:
- `write_agent(agent_id, name, url, plan_id, pricing, tags, description, category, team_name)` — upsert agent
- `read_agents(status=None)` — fetch all or filtered by status
- `update_agent_status(agent_id, status)` — update status
- `write_probe(agent_id, query, response, credits_spent, latency_ms, response_bytes, http_status, error)` → returns probe_id
- `read_probes(agent_id=None, limit=50)` — fetch probes
- `write_evaluation(agent_id, evaluator, metrics, summary, probe_id)` → returns eval_id
- `read_evaluations(agent_id=None, evaluator=None, limit=50)` — fetch evals (metrics auto-parsed from JSON)
- `write_ledger(direction, credits, purpose, agent_id, detail)` — record credit flow
- `get_pnl()` → `{"revenue": int, "spent": int, "margin": int}`
- `read_portfolio()` → list of portfolio view rows
- `get_top_agents(limit=10)` → agents sorted by eval_count DESC, avg_cost ASC

**Thread Safety**: Thread-local connections, WAL mode, 5s busy timeout.

---

## 4. Current State of src/buy_impl.py

**File**: `/Users/tk/Desktop/nevermind-agent/src/buy_impl.py` (136 lines)

**Functions**:

### `build_token_options(payments: Payments, plan_id: str) -> X402TokenOptions`
- Resolves scheme (nvm:credits or nvm:card-delegation)
- For card-delegation: builds CardDelegationConfig with spending_limit_cents=10_000, duration_secs=604_800 (7 days), currency="usd"
- Raises ValueError if fiat plan has no payment methods

### `purchase_data_impl(payments, plan_id, seller_url, query, agent_id=None) -> dict`
- Returns: `{"status": "success"|"payment_required"|"error", "content": [{"text": "..."}], "response": "...", "credits_used": int}`
- Flow:
  1. Build token options
  2. Get x402 access token
  3. POST to `{seller_url}/data` with `payment-signature` header
  4. Handle 402 (payment required), 200 (success), other errors
- Catches httpx.ConnectError, generic exceptions

### `check_balance_impl(payments, plan_id) -> dict`
- Returns: `{"status": "success"|"error", "balance": int, "is_subscriber": bool}`
- Calls `payments.plans.get_plan_balance(plan_id)`

### `discover_pricing_impl(seller_url) -> dict`
- Returns: `{"status": "success"|"error", "plan_id": str, "agent_id": str, "tiers": dict}`
- GET `{seller_url}/pricing`, expects JSON with planId, agentId, tiers

---

## 5. Current State of src/evaluation.py

**File**: `/Users/tk/Desktop/nevermind-agent/src/evaluation.py` (102 lines)

**Evaluator Protocol**:
```python
async def evaluator(agent_id: str, sheet: CentralSheet, **kwargs) -> None:
    # Read probes, produce metrics, write to evaluations table
```

**EvaluationPipeline Class**:
- `register(name: str, evaluator: Evaluator)` — register sub-agent
- `unregister(name: str)` — remove sub-agent
- `evaluator_names` property — list of registered names
- `async run(agent_id: str, sheet: CentralSheet, **kwargs)` — run all evaluators concurrently
  - Pre-check: only runs if agent has successful probes (error is None)
  - If no successful probes: marks agent as "dead"
  - Runs all evaluators in parallel via `asyncio.gather()`
  - Updates agent status to "evaluated" after all complete
  - Failures in one evaluator don't block others (wrapped in try/except)

**Global Instance**: `pipeline = EvaluationPipeline()`

---

## 6. Consulting Agent Status

**No existing consulting_agent.py file found.**

The smoke test seller has a hardcoded `process_consulting_query(query)` function that returns:
```python
{
    "status": "success",
    "query": query,
    "advice": "This is a smoke test response. In production, this would be real consulting advice.",
    "confidence": 0.95,
}
```

For Slice 6, we need to replace this with a real consulting agent (likely using strands-agents or Bedrock).

---

## 7. pyproject.toml

**File**: `/Users/tk/Desktop/nevermind-agent/pyproject.toml` (32 lines)

**Dependencies**:
- `strands-agents` >=1.0.0 (extras: openai, a2a)
- `payments-py` >=1.3.3 (extras: strands, a2a, langchain)
- `httpx` ^0.28.0
- `fastapi` ^0.120.0
- `uvicorn` >=0.34.2,<1.0.0
- `boto3` >=1.35.0
- `python-dotenv` ^1.0.0

**Dev Dependencies**:
- `pytest` ^8.0.0
- `pytest-asyncio` >=1.2.0,<2.0.0

**Scripts**:
- `server = "src.main:main"` — main portfolio manager
- `smoke-seller = "src.smoke.seller:main"` — smoke test seller
- `smoke-buyer = "src.smoke.buyer:main"` — smoke test buyer

---

## 8. .env.example

**File**: `/Users/tk/Desktop/nevermind-agent/.env.example` (31 lines)

**Key Variables**:
- `NVM_API_KEY` — Nevermined API key (builder for seller, subscriber for buyer)
- `NVM_ENVIRONMENT` — sandbox or mainnet
- `NVM_AGENT_ID` — auto-populated after registration
- `NVM_PLAN_ID`, `NVM_PLAN_ID_USDC`, `NVM_PLAN_ID_FIAT` — plan IDs
- `AWS_REGION` — default us-east-1
- `PORT` — default 3000
- `OUR_HOST` — default http://localhost:3000
- `SCAN_INTERVAL` — default 300 seconds
- `SELLER_URL` — target seller for buyer smoke test

---

## 9. Tests Overview

**Test Files**:
- `test_buy_impl.py` — mocked tests for purchase_data_impl, check_balance_impl, discover_pricing_impl, build_token_options
- `test_central_sheet.py` — SQLite operations
- `test_config.py` — environment loading
- `test_evaluation.py` — pipeline registration, run, gate evaluator logic
- `test_probe_runner.py` — probe execution with mocked payments
- `test_scanner.py` — discovery API, SDK enrichment, agent merging

**Pattern**: Heavy use of unittest.mock (Mock, patch, AsyncMock) for isolation. No integration tests against real Nevermined API.

---

## 10. Key Gotchas & Integration Points

### For Slice 6 (Consulting Agent + Real Server):

1. **Seller Replacement**: Currently `src/smoke/seller.py` is a hardcoded smoke test. Slice 6 needs a real consulting agent that:
   - Accepts queries via POST /data with x402 payment header
   - Uses strands-agents or Bedrock to generate real consulting advice
   - Returns `{"result": {...}, "credits_used": int}` format (see seller.py line 183-186)

2. **Main.py Integration**: The main server already has:
   - Evaluation pipeline wired up
   - Scanner loop running in background
   - Portfolio endpoint
   - No consulting agent endpoint yet — may need to add one for direct queries

3. **Payment Flow**: 
   - Buyer calls `purchase_data_impl()` which handles x402 token generation and settlement
   - Seller must verify token via `payments.facilitator.verify_permissions()` before executing
   - Seller settles via `payments.facilitator.settle_permissions()` after success
   - Both use `build_payment_required()` helper

4. **Ledger Tracking**: 
   - Probes write "out" ledger entries with purpose="probe"
   - Consulting queries should write "out" entries with purpose="consulting_upstream" (if buying from other agents)
   - Revenue should write "in" entries with purpose="consulting_revenue" (if selling consulting)

5. **Evaluator Pattern**:
   - New evaluators can be registered via `pipeline.register(name, async_callable)`
   - Each evaluator reads probes, produces metrics dict, writes to evaluations table
   - Runs after probes complete (via eval_callback in scanner)

6. **Config Loading**: 
   - `src/config.py` loads .env from main worktree (handles git worktrees)
   - All env vars exported as module-level constants

7. **Thread Safety**:
   - CentralSheet uses thread-local SQLite connections
   - Scanner runs in background task (asyncio)
   - Probe runner uses `asyncio.to_thread()` for sync buy_impl calls

---

## File Paths Summary

```
/Users/tk/Desktop/nevermind-agent/
├── src/
│   ├── main.py                    # FastAPI server, endpoints, startup
│   ├── config.py                  # Env loading
│   ├── central_sheet.py           # SQLite portfolio DB
│   ├── buy_impl.py                # x402 payment functions
│   ├── evaluation.py              # Pipeline + Evaluator protocol
│   ├── probe_runner.py            # Query execution against agents
│   ├── scanner.py                 # Discovery API + scan loop
│   ├── evaluators/
│   │   └── gate.py                # Binary pass/fail evaluator
│   └── smoke/
│       ├── seller.py              # Smoke test seller (to be replaced)
│       ├── buyer.py               # Smoke test buyer script
│       └── pricing.py             # Pricing config
├── tests/
│   ├── test_buy_impl.py
│   ├── test_central_sheet.py
│   ├── test_evaluation.py
│   ├── test_probe_runner.py
│   ├── test_scanner.py
│   └── test_config.py
├── pyproject.toml
├── .env.example
└── portfolio.db                   # SQLite database (created at runtime)
```

---

## Slice 6 Implementation Checklist

- [ ] Create `src/consulting_agent.py` with real LLM-backed consulting logic
- [ ] Update `src/smoke/seller.py` to use consulting agent instead of hardcoded response
- [ ] Add consulting agent endpoint to `src/main.py` (optional, for direct queries)
- [ ] Register consulting evaluator in main.py startup (if needed)
- [ ] Update ledger tracking for consulting revenue/upstream costs
- [ ] Add tests for consulting agent integration
- [ ] Update .env.example with any new consulting-specific vars (e.g., LLM model, API keys)

