# Portfolio Manager Agent — Slice Plan

> Run `/cm` with any slice block below. Slices are ordered by dependency.
> After Slice 1 merges: `cp .env.example .env` and fill in keys. All worktrees resolve from main.
Read PLAN_V3 for full context always and always use .venv

## Environment

| Key | Service | First needed |
|-----|---------|-------------|
| `NVM_API_KEY` | Nevermined (builder key for selling, subscriber key for buying) | Slice 2 |
| `NVM_ENVIRONMENT` | Nevermined (sandbox / staging_sandbox / live) | Slice 2 |
| `NVM_PLAN_ID` | Nevermined plan ID (auto-registered or manual) | Slice 2 |
| `NVM_AGENT_ID` | Nevermined agent DID (auto-registered or manual) | Slice 2 |
| `OPENAI_API_KEY` | OpenAI (for basic seller LLM — smoke test only) | Slice 2 |
| `AWS_REGION` | AWS Bedrock | Slice 6 |
| `NVM_PLAN_ID_USDC` | Nevermined USDC plan ID | Slice 6 |
| `NVM_PLAN_ID_FIAT` | Nevermined fiat/Stripe plan ID | Slice 6 |
| `OUR_HOST` | Public-facing URL for agent registration | Slice 6 |
| `SCAN_INTERVAL` | Scanner loop interval in seconds | Slice 4 |
| `SELLER_URL` | Target seller for buyer smoke test | Slice 2 |

## Slice Index

| # | Name | Spec sections | Depends on | Est. Level |
|---|------|--------------|------------|------------|
| 1 | Scaffold | Stack, File Structure, pyproject.toml | — | L1 |
| 2 | Seller + Buyer Smoke Test | x402 Payment Flow, Key Syntax Patterns (seller/buyer) | 1 | L2 |
| 3 | Central Sheet + Buy Impl | Component 1, Component 3 (buy_impl only) | 1 | L2 |
| 4 | Scanner + Probe Runner | Component 2, Component 3 | 3 | L3 |
| 5 | Evaluation Pipeline | Component 4, evaluators/gate.py | 3, 4 | L2 |
| 6 | Consulting Agent + Real Server | Component 5, Component 6, Deployment | 3, 4, 5, 2 | L3 |
| 7 | Quality Scorers + Marketplace | Extensibility Points, evaluators/quality_judge.py | 5, 6 | L2 |

## Dependency Graph

```
  1 Scaffold
  ├──→ 2 Smoke Test (seller+buyer)   ──────────────────────┐
  └──→ 3 Central Sheet + buy_impl                          │
       └──→ 4 Scanner + Probe Runner                       │
            └──→ 5 Evaluation Pipeline                     │
                 └──→ 6 Consulting Agent + Real Server  ◀──┘
                      └──→ 7 Quality Scorers + Marketplace
```

Parallel: Slice 2 and Slice 3 can run concurrently (no shared deps beyond Slice 1).

---

### Slice 1 — Scaffold

**Read:** Stack table, File Structure, pyproject.toml, .env.example

**Deliver:**
- `pyproject.toml`: Poetry config with all deps (strands-agents, payments-py, fastapi, uvicorn, httpx, boto3, python-dotenv). Scripts: `server`, `smoke-seller`, `smoke-buyer`.
- `src/__init__.py`: empty
- `src/config.py`: worktree-aware env loading (`.env` from main worktree), exports all env vars as typed constants
- `src/central_sheet.py`: **stub only** — class `CentralSheet` with `__init__` and pass-through methods that raise `NotImplementedError`. Enough for downstream imports.
- `src/buy_impl.py`: **stub only** — function signatures for `purchase_data_impl`, `check_balance_impl`, `discover_pricing_impl`, `build_token_options` with `NotImplementedError`.
- `src/evaluation.py`: **stub only** — `EvaluationPipeline` class with `register()`, `run()` stubs.
- `src/evaluators/__init__.py`: empty
- `src/main.py`: **stub only** — FastAPI app with `/health` returning `{"status": "ok"}`, nothing else wired.
- `.env.example`: already created
- `.gitignore`: Python defaults + `.env`, `portfolio.db`, `__pycache__`, `.venv`
- `agents_config.json`: empty array `[]`
- `tests/__init__.py`, `tests/test_config.py`: verify config loads, env vars resolve

**Fixtures produced:** none — hand-written mocks
**Fixtures required:** none

**Acceptance:**
- `poetry install` succeeds with no errors
- `poetry run python -c "from src.config import NVM_ENVIRONMENT; print(NVM_ENVIRONMENT)"` prints `sandbox` (or .env value)
- `poetry run server` starts and `curl localhost:3000/health` returns `{"status":"ok"}`
- `pytest tests/test_config.py` passes

---

### Slice 2 — Seller + Buyer Smoke Test

**Read:** PLAN_V3 "Key Syntax Patterns" sections (seller-side, buyer-side, FastAPI endpoint, registration). Reference `nevermined-io/hackathons/agents/seller-simple-agent/src/agent.py`, `strands_agent.py`, `pricing.py` and `buyer-simple-agent/src/client.py`, `tools/purchase.py`, `tools/discover.py`, `tools/balance.py`, `tools/token_options.py`.

**Purpose:** Validate Nevermined plumbing end-to-end before building the real system. This is a **temporary test harness** — Slice 6 replaces it with the real consulting agent.

**Deliver:**
- `src/smoke/seller.py`: Minimal FastAPI seller copied from hackathon pattern. One `@tool(context=True) @requires_payment(...)` tool that returns hardcoded consulting data. `POST /data`, `GET /pricing`, `GET /health`. Uses OpenAI model (gpt-4o-mini) for simplicity — no Bedrock needed yet. Registers agent+plan with Nevermined if `NVM_AGENT_ID` not set (use `payments.agents.register_agent_and_plan()`).
- `src/smoke/buyer.py`: Scripted x402 buyer (no LLM). Steps: discover pricing → check balance → purchase data → print result. Copied from buyer-simple-agent/src/client.py pattern.
- `src/smoke/__init__.py`: empty
- `src/smoke/pricing.py`: Single tier `{"consulting": {"credits": 1, "description": "Test consulting query"}}`.
- Add to `pyproject.toml` scripts: `smoke-seller = "src.smoke.seller:main"`, `smoke-buyer = "src.smoke.buyer:main"`

**Fixtures produced:** none
**Fixtures required:** none

**Acceptance:**
- `poetry run smoke-seller` starts on `:3000`, prints plan ID
- `curl localhost:3000/pricing` returns JSON with `planId` and `tiers`
- `poetry run smoke-buyer` completes full x402 flow: discovers pricing, checks balance, purchases data, prints response and credits_used
- Seller logs show payment settlement with `credits_redeemed > 0`

---

### Slice 3 — Central Sheet + Buy Impl

**Read:** PLAN_V3 Component 1 (Central Sheet — full schema, all methods), Component 3 (buy_impl.py — `purchase_data_impl`, `check_balance_impl`, `discover_pricing_impl`, `build_token_options`).

**Deliver:**
- `src/central_sheet.py`: **Full implementation** replacing the stub. SQLite WAL mode, thread-safe. Tables: `agents`, `probes`, `evaluations`, `ledger`. View: `portfolio`. All CRUD methods from PLAN_V3 Component 1.
- `src/buy_impl.py`: **Full implementation** replacing the stub. Copy `_impl` functions from `nevermined-io/hackathons/agents/buyer-simple-agent/src/tools/`. Functions: `purchase_data_impl`, `check_balance_impl`, `discover_pricing_impl`, `build_token_options`. Framework-agnostic, explicit args, no decorators.
- `tests/test_central_sheet.py`: Tier 1 (mocked). Test all CRUD: write/read agents, write/read probes, write/read evaluations, write/read ledger, get_pnl, read_portfolio view, get_top_agents. Test upsert behavior, status transitions.
- `tests/test_buy_impl.py`: Tier 1 (mocked httpx). Test purchase success, 402 response, connection error, balance check. Tier 2 (manual): test against smoke seller from Slice 2.

**Fixtures produced:** `tests/fixtures/sample_agents.json` — 3 fake agent dicts for downstream tests
**Fixtures required:** none

**Acceptance:**
- `pytest tests/test_central_sheet.py` — all pass, SQLite creates tables, CRUD works, portfolio view aggregates correctly
- `pytest tests/test_buy_impl.py` — all Tier 1 pass with mocked httpx
- `python -c "from src.central_sheet import CentralSheet; s = CentralSheet(':memory:'); s.write_agent('test', 'Test', 'http://x', 'plan1'); print(s.read_agents())"` prints agent list

---

### Slice 4 — Scanner + Probe Runner

**Read:** PLAN_V3 Component 2 (Scanner — `discover_from_hackathon_api`, `discover_from_sdk`, `probe_pricing`, `scan_loop`, `agents_config.json` format), Component 3 (Probe Runner — `run_probe`, `DEFAULT_QUERIES`).

**Deliver:**
- `src/scanner.py`: Full implementation. Discovery priority: (1) Hackathon Discovery API `GET https://nevermined.ai/hackathon/register/api/discover?side=sell`, (2) Nevermined SDK `get_agent()`, (3) `agents_config.json` fallback. Merge logic, diff against known agents, spawn probes for new ones.
- `src/probe_runner.py`: Full implementation. Stateless async probes using `buy_impl.purchase_data_impl`. Writes probes + ledger to sheet. Calls eval_callback when probes complete.
- `agents_config.json`: Update with example structure (commented).
- `src/main.py`: Wire scanner as `asyncio.create_task` in startup. Add `/portfolio` endpoint that reads from sheet.
- `tests/test_scanner.py`: Tier 1 — mock httpx for Discovery API, test agent merging logic, test diff (new vs known). Tier 2 — hit real Discovery API with NVM_API_KEY.
- `tests/test_probe_runner.py`: Tier 1 — mock `purchase_data_impl`, verify sheet writes. Tier 2 — probe smoke seller.

**Fixtures produced:** `tests/fixtures/discovery_api_response.json` — sample Discovery API JSON
**Fixtures required:** `tests/fixtures/sample_agents.json` from Slice 3

**Acceptance:**
- `pytest tests/test_scanner.py tests/test_probe_runner.py` — all Tier 1 pass
- `poetry run server` starts, scanner runs in background, `/portfolio` returns JSON
- Scanner logs show "Scan complete: N new, M known" every `SCAN_INTERVAL` seconds

---

### Slice 5 — Evaluation Pipeline

**Read:** PLAN_V3 Component 4 (Evaluation Pipeline — `EvaluationPipeline`, `Evaluator` protocol, `pipeline` global instance), evaluators/gate.py.

**Deliver:**
- `src/evaluation.py`: **Full implementation** replacing the stub. `EvaluationPipeline` with `register()`, `unregister()`, `run()`, concurrent evaluator dispatch via `asyncio.gather`. `Evaluator` Protocol definition.
- `src/evaluators/gate.py`: Binary pass/fail evaluator. >50% error rate → mark dead. No LLM needed.
- `src/evaluators/__init__.py`: empty
- Wire pipeline into `src/main.py` startup: register gate evaluator. Wire `eval_callback` into probe_runner.
- `tests/test_evaluation.py`: Tier 1 — mock sheet, test pipeline dispatches to registered evaluators, test gate logic with synthetic probes (100% success, 50/50, 100% fail).

**Fixtures produced:** none
**Fixtures required:** `tests/fixtures/sample_agents.json` from Slice 3

**Acceptance:**
- `pytest tests/test_evaluation.py` — all pass
- Manual: start server, wait for scanner → probes → gate evaluates → agent status changes to `evaluated` or `dead`
- `/portfolio` shows `eval_count > 0` for probed agents

---

### Slice 6 — Consulting Agent + Real Server

**Read:** PLAN_V3 Component 5 (Consulting Agent — `create_consulting_agent`, tools, system prompt), Component 6 (Web Server — full `main.py`), Deployment section, Key Syntax Patterns (registration, dual plans).

**Purpose:** Replace Slice 2's smoke seller with the real consulting agent. This is the revenue-generating seller.

**Deliver:**
- `src/consulting_agent.py`: Full Strands agent with `@requires_payment`. Tools: `consulting_query` (billable), `read_portfolio`, `get_agent_report`, `buy_from_agent`. Uses `BedrockModel(model_id="us.anthropic.claude-sonnet-4-6")`.
- `src/main.py`: **Full implementation.** `register_if_needed()` for dual payment plans (USDC + Fiat). `POST /data` with payment-signature extraction and settlement tracking. `GET /pricing` exposing both rails. `GET /portfolio` with P&L. `GET /health` with full stats. Agent lock for concurrent request serialization.
- Remove or archive `src/smoke/` — it served its purpose.
- `.bedrock_agentcore.yaml`: AgentCore deployment config.
- `tests/test_consulting_agent.py`: Tier 1 — mock model + sheet, test tool dispatch. Tier 2 — test with real Bedrock model.

**Fixtures produced:** none
**Fixtures required:** `tests/fixtures/sample_agents.json`, `tests/fixtures/discovery_api_response.json`

**Acceptance:**
- `poetry run server` starts, registers agent + plans with Nevermined, logs agent ID and plan IDs
- `curl localhost:3000/pricing` returns both USDC and fiat plan IDs
- `curl localhost:3000/health` returns agent count, probe count, P&L
- Smoke buyer from Slice 2 (or another team's buyer) can purchase consulting via x402

---

### Slice 7 — Quality Scorers + Marketplace Polish

**Read:** PLAN_V3 Component 4 (evaluators/quality_judge.py placeholder), Extensibility Points table, user-provided marketplace registration steps.

**Deliver:**
- `src/evaluators/quality_judge.py`: LLM-based quality scorer. Strands sub-agent that reads probes, scores response quality, writes ROI metric (`quality_score / credits_spent`). Factory pattern: `create_quality_judge(model)`.
- Register quality_judge in `src/main.py` startup.
- `src/main.py` enhancements: richer `/portfolio` endpoint with quality scores, ROI rankings, recommended agents.
- Marketplace metadata: ensure agent registration includes description, category, tags, services offered, services per request — all fields needed to appear in the hackathon marketplace.
- `tests/test_quality_judge.py`: Tier 1 — mock model, verify evaluation writes correct metrics schema.

**Fixtures produced:** none
**Fixtures required:** all prior fixtures

**Acceptance:**
- `pytest tests/test_quality_judge.py` — passes with mocked model
- `/portfolio` returns agents ranked by ROI with quality scores
- Agent visible in hackathon marketplace (synced from Nevermined)
- Marketplace listing shows: description, category, services offered, pricing for both rails
