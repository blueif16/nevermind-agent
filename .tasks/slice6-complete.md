# Slice 6 — Consulting Agent + Real Server ✅

**Status**: COMPLETE
**Commit**: 99aa308

---

## What Was Accomplished

Replaced the Slice 2 smoke test seller with a production-ready consulting agent backed by Claude Sonnet 4.6 via AWS Bedrock. This is the revenue-generating core of the portfolio manager.

### 1. Consulting Agent (`src/consulting_agent.py`)

Created a Strands agent with four tools:

- **`consulting_query`** (billable) — Entry point with `@requires_payment` decorator. Charges 1 credit per query.
- **`read_portfolio`** — Returns ranked agents with quality data, P&L, and probe counts
- **`get_agent_report`** — Detailed assessment of a specific agent (probes + evaluations)
- **`buy_from_agent`** — Purchases data from upstream agents, logs to ledger with purpose="consulting_upstream"

System prompt guides two workflows:
- (a) Marketplace intelligence — agent rankings, cost comparisons, quality analysis
- (b) Data fulfillment — buy from top agents, synthesize outputs, provide cost breakdown

### 2. Full Server Implementation (`src/main.py`)

Replaced stub endpoints with production implementation:

**Registration**:
- `register_if_needed()` — Dual payment plan registration (USDC + Fiat)
- USDC plan: 10 USDC for 100 credits on Base Sepolia
- Fiat plan: $9.99 for 100 credits via Stripe (test card: 4242 4242 4242 4242)

**Endpoints**:
- `POST /data` — x402 payment flow with agent_lock for concurrency
  - Extracts payment-signature header
  - Runs consulting agent with invocation_state
  - Checks payment_required vs settlement
  - Records revenue to ledger (direction="in", purpose="consulting_revenue")
- `GET /pricing` — Returns both payment rails (USDC + Fiat)
- `GET /portfolio` — Enhanced with evaluator names
- `GET /health` — Full stats (agents tracked, evaluated, total probes, P&L)

**Background Tasks**:
- Scanner loop (from Slice 4) continues running
- Evaluation pipeline (from Slice 5) continues running

### 3. Smoke Test Archive

Moved `src/smoke/` to `src/smoke_archive/` to preserve reference implementation. Removed smoke-seller and smoke-buyer scripts from pyproject.toml.

### 4. Environment Configuration

Updated `.env.example`:
- Added `NVM_PLAN_ID_USDC` — Crypto payment rail
- Added `NVM_PLAN_ID_FIAT` — Fiat payment rail
- Added comment for `OUR_HOST` — Public-facing URL for agent registration
- Clarified `NVM_PLAN_ID` as primary plan used by @requires_payment

### 5. Comprehensive Tests

Created `tests/test_consulting_agent.py` with 8 tests (all passing):
- Agent creation with correct tools
- read_portfolio tool returns portfolio data
- get_agent_report tool returns agent details
- buy_from_agent success with ledger tracking
- buy_from_agent handles agent not found
- buy_from_agent doesn't write ledger on failure
- consulting_query tool exists with payment decorator
- Agent has system prompt configured

---

## Files Changed

```
.env.example                      # Added dual plan IDs, OUR_HOST comment
pyproject.toml                    # Removed smoke test scripts
src/consulting_agent.py           # NEW - 200 lines
src/main.py                       # REPLACED - 330 lines (was 74)
src/smoke/ → src/smoke_archive/   # ARCHIVED
tests/test_consulting_agent.py    # NEW - 230 lines
```

---

## Verification

✅ All tests pass: `pytest tests/test_consulting_agent.py` (8 passed)
✅ Server imports: `python -c "from src.main import app; print('OK')"`
✅ Consulting agent imports: `python -c "from src.consulting_agent import create_consulting_agent; print('OK')"`
✅ No smoke test references in pyproject.toml
✅ Smoke test code preserved in src/smoke_archive/

---

## Next Steps

**To run the server**:
```bash
# 1. Ensure .env is configured with NVM_API_KEY and AWS credentials
cp .env.example .env
# Fill in: NVM_API_KEY, AWS_REGION (or use EC2 instance profile)

# 2. Start the server
poetry run server

# Server will:
# - Register agent + dual payment plans with Nevermined (if not already registered)
# - Log agent ID and plan IDs
# - Start scanner in background
# - Listen on http://0.0.0.0:3000
```

**Endpoints available**:
- `GET /health` — Server status with agent counts and P&L
- `GET /pricing` — Payment plans (USDC + Fiat)
- `GET /portfolio` — Ranked agents with evaluation data
- `POST /data` — Consulting queries (requires x402 payment)

**Integration with Slice 2 smoke buyer**:
The smoke buyer from Slice 2 (archived in `src/smoke_archive/buyer.py`) can purchase consulting from this server by pointing `SELLER_URL` to this server's `/data` endpoint.

---

## Key Patterns Validated

1. **Strands Agent API**: Tools accessed via `agent.tool_registry.registry[name]`, not `agent.tools`
2. **Payment Flow**: `@requires_payment` decorator + `invocation_state={"payment_token": ...}` + `extract_payment_required()` + settlement tracking
3. **Dual Payment Rails**: Register USDC plan with `register_agent_and_plan()`, then fiat plan with `register_credits_plan()`
4. **Ledger Tracking**: "in" for revenue, "out" for upstream costs, purpose field distinguishes consulting vs probes
5. **Concurrency**: `agent_lock = asyncio.Lock()` serializes requests to non-thread-safe Strands agent

---

## Dependencies on Previous Slices

- **Slice 1**: Scaffold (config, stubs, pyproject.toml)
- **Slice 2**: x402 payment patterns validated in smoke test
- **Slice 3**: CentralSheet for portfolio access, buy_impl for upstream purchases
- **Slice 4**: Scanner continues running in background
- **Slice 5**: Evaluation pipeline continues running, gate evaluator registered

---

## What's NOT Done Yet (Future Slices)

- **Slice 7**: Quality scorers (LLM-based evaluation), marketplace polish
- Real Bedrock testing (requires AWS credentials with Bedrock access)
- End-to-end x402 payment testing (requires funded USDC wallet or Stripe test mode)
- Deployment to EC2 (deployment section in PLAN_V3 has instructions)

---

## Notes

- The consulting agent uses Claude Sonnet 4.6 via Bedrock (`us.anthropic.claude-sonnet-4-6`)
- Payment decorator is from `payments_py.x402.strands.requires_payment`
- Agent is not thread-safe, so we use `agent_lock` to serialize concurrent requests
- Fiat plan registration may fail in some environments (non-fatal, USDC plan still works)
- All tests use mocked dependencies (no real Bedrock or Nevermined API calls)
