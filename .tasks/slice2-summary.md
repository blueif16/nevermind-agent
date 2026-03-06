# Slice 2 — Implementation Complete ✅

## What Was Built

### 1. Smoke Test Seller (`src/smoke/seller.py`)
- **FastAPI server** with x402 payment integration
- **Manual verify + settle pattern** (following Nevermined docs)
- **Auto-registration**: Creates agent + plan on startup if not configured
- **Endpoints**:
  - `GET /health` - Health check
  - `GET /pricing` - Returns plan ID and pricing tiers
  - `POST /data` - Payment-protected consulting endpoint
- **Payment flow**:
  1. Extract `payment-signature` header
  2. Verify permissions (doesn't burn credits)
  3. Process business logic
  4. Settle permissions (burns credits)
  5. Return 402 if payment missing/invalid

### 2. Smoke Test Buyer (`src/smoke/buyer.py`)
- **Scripted x402 buyer** (no LLM needed)
- **Full purchase flow**:
  1. Discover pricing from seller
  2. Check balance
  3. Purchase data via x402 token
  4. Print result + credits used
- Uses existing `buy_impl.py` functions from Slice 3

### 3. Supporting Files
- `src/smoke/__init__.py` - Module marker
- `src/smoke/pricing.py` - Test pricing config
- `.tasks/slice2-smoke-test.md` - Execution plan and status

## Key Implementation Decisions

### ✅ Used Manual Pattern (Not Strands Decorator)
- PLAN_V3 referenced `@requires_payment` decorator that doesn't exist in current `payments-py`
- Switched to manual `verify_permissions` → `settle_permissions` pattern from Nevermined docs
- **This is the correct approach** - same pattern will be used in Slice 6

### ✅ Lazy Initialization
- Payments client initialized on first use (not at import time)
- Allows imports to succeed even without .env configured
- Prevents errors during development/testing

### ✅ Auto-Registration
- Seller registers agent + plan on startup if `NVM_AGENT_ID` / `NVM_PLAN_ID` not set
- Logs the IDs for user to save to .env
- Simplifies first-time setup

## Testing Status

### ✅ Completed
- T1: Module structure + pricing config
- T2: Seller implementation (imports verified)
- T3: Buyer implementation (imports verified)

### ⏳ Blocked (Requires .env)
- T4: End-to-end integration test

## Next Steps for User

1. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env and set:
   # NVM_API_KEY=sandbox:your-builder-key-here
   # SELLER_URL=http://localhost:3000
   ```

2. **Get API key** from https://nevermined.app/settings/api-keys
   - Use "builder" key for seller
   - Use "subscriber" key for buyer (or same key for testing)

3. **Run smoke test**:
   ```bash
   # Terminal 1 - Start seller
   poetry run smoke-seller

   # Terminal 2 - Run buyer
   poetry run smoke-buyer
   ```

4. **Expected output**:
   - Seller: Registers agent+plan, prints IDs, starts on :3000
   - Buyer: Discovers pricing, checks balance, purchases data, prints result
   - Seller logs: Shows payment settlement with credits_redeemed > 0

## Acceptance Criteria Status

| Criteria | Status |
|----------|--------|
| `poetry run smoke-seller` starts on :3000, prints plan ID | ⏳ Ready (needs .env) |
| `curl localhost:3000/pricing` returns JSON with planId and tiers | ⏳ Ready (needs .env) |
| `poetry run smoke-buyer` completes full x402 flow | ⏳ Ready (needs .env) |
| Seller logs show payment settlement with credits_redeemed > 0 | ⏳ Ready (needs .env) |

## Files Changed

```
src/smoke/__init__.py          (new)
src/smoke/pricing.py           (new)
src/smoke/seller.py            (new)
src/smoke/buyer.py             (new)
.tasks/slice2-smoke-test.md    (new)
```

## Git Commit

```
84c6f32 feat: implement Slice 2 - Seller + Buyer Smoke Test
```

---

**Slice 2 implementation is complete and ready for testing once .env is configured.**
