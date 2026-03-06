# Slice 4 Implementation Findings

## Scanner Discovery Flow (Component 2)

**Priority order:**
1. **Hackathon Discovery API** (primary) — `GET https://nevermined.ai/hackathon/register/api/discover?side=sell` with `x-nvm-api-key` header. Returns structured sellers with `nvmAgentId`, `endpointUrl`, `planIds[]`, `pricing`, `category`, `teamName`.
2. **Nevermined SDK** (secondary) — `payments.agents.get_agent(agent_id)` for enrichment. Extracts URL from `endpoints[]` array (POST method).
3. **agents_config.json** (fallback) — Currently empty `[]` in repo. Format: array of objects with `agent_id`, `name`, `url`, `plan_id` (optional fields).

**Key functions needed:**
- `discover_from_hackathon_api(nvm_api_key: str) -> list[dict]` — async, returns agents with all fields
- `discover_from_sdk(payments: Payments, agent_ids: list[str]) -> list[dict]` — async, enriches metadata
- `load_known_agents(config_path: str) -> list[dict]` — sync, loads fallback config
- `probe_pricing(url: str) -> dict` — async, GET `/pricing` endpoint, returns tiers dict
- `scan_loop(sheet, payments, probe_callback, interval, nvm_api_key)` — main async loop, diffs against known agents, spawns probes for new ones

**Merge strategy:** Config (lowest) → SDK (enriches) → API (overwrites, highest priority). Diff against `sheet.read_agents()` to find new agents.

## Probe Runner (Component 3)

**Signature:** `async def run_probe(agent_info: dict, sheet: CentralSheet, payments: Payments, queries: list[str] | None = None, eval_callback=None)`

**DEFAULT_QUERIES:**
```python
[
    "What services do you provide and at what price?",
    "Give me a sample analysis of current AI market trends",
    "Provide concrete data points on agent marketplace adoption",
]
```

**Flow:**
1. Extract `agent_id`, `plan_id`, `url` from `agent_info`
2. Skip if missing `url` or `plan_id`
3. For each query, call `purchase_data_impl()` via `asyncio.to_thread()` (sync wrapper)
4. Record latency, response bytes, credits spent
5. Call `sheet.write_probe()` with result
6. If successful, call `sheet.write_ledger(direction="out", purpose="probe", ...)`
7. Update agent status: `"probed"` if any success, `"dead"` if all fail
8. Trigger `eval_callback(agent_id, sheet)` if provided

## CentralSheet Methods Used

- `write_agent(agent_id, name, url, plan_id, pricing, tags, description, category, team_name)` — upsert with ON CONFLICT
- `read_agents(status=None)` — filter by status or return all
- `update_agent_status(agent_id, status)` — set status to 'new'|'probed'|'evaluated'|'dead'|'reeval'
- `write_probe(agent_id, query, response, credits_spent, latency_ms, response_bytes, http_status, error)` — returns probe_id
- `write_ledger(direction, credits, purpose, agent_id, detail)` — direction='out'|'in', purpose='probe'|'consulting_upstream'|'consulting_revenue'

## buy_impl Functions

- `build_token_options(payments: Payments, plan_id: str) -> X402TokenOptions` — resolves scheme, handles card delegation
- `purchase_data_impl(payments, plan_id, seller_url, query, agent_id=None) -> dict` — returns `{status, content, response, credits_used}`. Status: 'success'|'payment_required'|'error'

## Environment Variables (from config.py)

- `NVM_API_KEY` — required for Discovery API
- `NVM_ENVIRONMENT` — default 'sandbox'
- `SCAN_INTERVAL` — default 300 seconds
- `NVM_PLAN_ID`, `NVM_AGENT_ID` — our registration IDs (set at startup)

## agents_config.json Format

Currently empty `[]`. Expected format:
```json
[
  {
    "agent_id": "did:nv:abc123...",
    "name": "Team Alpha Weather Agent",
    "url": "https://team-alpha.example.com",
    "plan_id": "did:nv:plan456..."
  }
]
```

## Key Patterns & Gotchas

1. **Agent ID fallback:** If `nvmAgentId` missing from API, use `endpointUrl` as ID
2. **Pricing enrichment:** Scanner probes `/pricing` for new agents before writing to sheet
3. **Status lifecycle:** new → probed → evaluated (or dead if all probes fail)
4. **Async/sync boundary:** `purchase_data_impl` is sync, wrapped with `asyncio.to_thread()` in probe runner
5. **Ledger tracking:** Every successful probe writes ledger entry with `direction="out"`, `purpose="probe"`
6. **Error handling:** Probe errors stored in `error` column (not `response`), `http_status=0` for exceptions
7. **Thread safety:** CentralSheet uses `threading.local()` for per-thread connections; WAL mode handles concurrent access
8. **Evaluation callback:** Optional; if provided, called after probes complete to trigger pipeline

## Testing Patterns

- Use `:memory:` SQLite for unit tests
- Mock `httpx.AsyncClient` for HTTP calls
- Mock `Payments` SDK methods
- Fixtures: `sample_agents.json` has 3 test agents with full pricing/tags/category/team_name
