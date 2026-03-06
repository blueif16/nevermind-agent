# Autonomous Agent Portfolio Manager

> **AI McKinsey meets Berkshire Hathaway.** We evaluate every agent in the marketplace, maintain a live ROI-ranked portfolio, and sell consulting where we buy from the best agents on the client's behalf.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Strands SDK](https://img.shields.io/badge/strands-agents-green.svg)](https://github.com/nevermined-io/strands-agents)
[![x402 Protocol](https://img.shields.io/badge/payment-x402-orange.svg)](https://docs.nevermined.io/docs/protocol/x402)

---

## 🎯 What It Does

This agent autonomously:

1. **Discovers** all agents in the Nevermined marketplace via the Discovery API
2. **Probes** each agent with test queries to measure quality, latency, and cost
3. **Evaluates** responses using pluggable scorer sub-agents (quality judges, comparative analysis, etc.)
4. **Ranks** agents by ROI (quality/cost) in a live portfolio
5. **Sells consulting** — clients pay us to either get intelligence reports or have us fulfill requests by buying from the best agents on their behalf

**Revenue model:** We charge 1 credit per consulting query. We spend credits buying from upstream agents. The portfolio tells us who delivers the best ROI.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Web Server                        │
│  POST /data (consulting) • GET /pricing • GET /portfolio    │
└────────────┬────────────────────────────────┬───────────────┘
             │                                │
    ┌────────▼────────┐              ┌───────▼────────┐
    │ Consulting Agent │              │ Scanner (bg)   │
    │ @requires_payment│              │ discovers new  │
    │ reads portfolio  │              │ agents, spawns │
    │ buys upstream    │              │ probes         │
    └────────┬─────────┘              └───────┬────────┘
             │                                │
             │         ┌──────────────┐       │
             └────────▶│ Central Sheet│◀──────┘
                       │  (SQLite)    │
                       │              │
                       │ • agents     │
                       │ • probes     │
                       │ • evaluations│
                       │ • ledger     │
                       │ • portfolio  │
                       └──────┬───────┘
                              │
              ┌───────────────┴───────────────┐
              │                               │
     ┌────────▼────────┐          ┌──────────▼─────────┐
     │  Probe Runner   │          │ Evaluation Pipeline│
     │  (stateless)    │          │ (pluggable scorers)│
     │  calls agents   │          │ writes metrics     │
     │  logs results   │          │ ranks by ROI       │
     └─────────────────┘          └────────────────────┘
```

**Key design choices:**

- **SQLite WAL** — single-process server, zero setup, perfect for hackathon velocity
- **Raw Python probes** — no framework overhead, clean context isolation
- **Pluggable evaluators** — add new scorers by registering one function
- **x402 native** — universal payment substrate for both buying and selling
- **Dual payment rails** — accept USDC (crypto) or credit card (Stripe)

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- Nevermined API key ([get one here](https://nevermined.app))
- AWS credentials with Bedrock access (for Claude Sonnet 4.6)

### Local Development

```bash
# Clone and install
git clone <your-repo>
cd portfolio-manager-agent
poetry install

# Configure
cp .env.example .env
# Fill in: NVM_API_KEY, AWS credentials, OUR_HOST

# Run
poetry run server
```

Server starts on `http://localhost:3000`. Check `/health` to verify.

### EC2 Deployment

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
# Set: NVM_API_KEY, OUR_HOST=http://<public-ip>:3000

# 6. Run in tmux
tmux new -s agent
poetry run server
# Ctrl-B D to detach
```

**That's it.** SQLite lives at `./portfolio.db`. Scanner runs as an asyncio background task — no separate worker, no queue, no cron.

---

## 📡 API Endpoints

### `POST /data` — Consulting (x402 protected)

Client-facing endpoint. Requires payment via `payment-signature` header.

**Request:**
```json
{
  "query": "Which agents are best for weather data and what do they cost?"
}
```

**Response:**
```json
{
  "response": "Based on our portfolio, the top 3 weather agents are...",
  "credits_used": 1
}
```

### `GET /pricing` — Pricing Info

Returns plan IDs and tier details. Exposes both USDC and fiat payment rails.

```json
{
  "planId": "did:nv:...",
  "plans": {
    "usdc": "did:nv:...",
    "fiat": "did:nv:..."
  },
  "tiers": {
    "consulting": {
      "credits": 1,
      "description": "Intelligence query or upstream fulfillment"
    }
  },
  "payment_rails": {
    "usdc": "Pay with USDC on Base Sepolia",
    "fiat": "Pay with credit card via Stripe"
  }
}
```

### `GET /portfolio` — Live Portfolio

Public dashboard showing ranked agents, evaluation data, and P&L.

```json
{
  "portfolio": [
    {
      "agent_id": "did:nv:...",
      "name": "Weather Agent",
      "probe_count": 12,
      "avg_cost": 5,
      "avg_latency": 234.5,
      "eval_count": 3,
      "status": "evaluated"
    }
  ],
  "pnl": {
    "revenue": 100,
    "spent": 45,
    "margin": 55
  },
  "evaluators": ["gate", "quality_judge"]
}
```

### `GET /health` — Health Check

```json
{
  "status": "ok",
  "agents_tracked": 23,
  "agents_evaluated": 18,
  "total_probes": 156,
  "pnl": {"revenue": 100, "spent": 45, "margin": 55}
}
```

---

## 🧩 Components

### 1. Central Sheet (`central_sheet.py`)

SQLite wrapper with thread-safe connections. All components read/write through this.

**Tables:**
- `agents` — discovered agents with metadata, pricing, status
- `probes` — raw purchase results (query, response, cost, latency, errors)
- `evaluations` — pluggable scorer outputs (JSON metrics + summary)
- `ledger` — credit P&L tracking (revenue in, spending out)
- `portfolio` — aggregated view (agents + probes + evaluations)

### 2. Scanner (`scanner.py`)

Background async task. Discovers agents from:
1. **Hackathon Discovery API** (primary) — all registered sellers
2. **Nevermined SDK** (secondary) — enriches with full metadata
3. **`agents_config.json`** (fallback) — manually added agents

Diffs against known agents, spawns probes for new ones.

### 3. Probe Runner (`probe_runner.py`)

Stateless fire-and-forget. Calls agents with test queries, logs results to `probes` table, triggers evaluation pipeline.

**No agent framework** — raw Python functions with explicit args. Clean context isolation.

### 4. Evaluation Pipeline (`evaluation.py`)

Registry of scorer sub-agents. Each evaluator:
1. Reads probes (and optionally other evaluations) from the sheet
2. Produces metrics (any JSON-serializable dict)
3. Writes results to `evaluations` table

**Built-in evaluators:**
- `gate` — binary pass/fail (>50% error rate = dead)
- `quality_judge` — LLM-based quality scoring (placeholder for your logic)

**Add new scorers:** Write an async function matching the `Evaluator` protocol, call `pipeline.register()`. One line.

### 5. Consulting Agent (`consulting_agent.py`)

Strands agent with `@requires_payment`. The revenue generator.

**Tools:**
- `consulting_query` — billable entry point (payment-protected)
- `read_portfolio` — ranked agents with quality data
- `get_agent_report` — detailed assessment of a specific agent
- `buy_from_agent` — purchase data from upstream agents

**Workflow:**
1. Client pays us 1 credit
2. Agent reads portfolio to identify top agents
3. Buys from 2-3 upstream agents (logs spending to ledger)
4. Synthesizes outputs and returns to client

### 6. Web Server (`main.py`)

Single FastAPI process. Hosts everything:
- Consulting endpoint (`/data`)
- Public endpoints (`/pricing`, `/portfolio`, `/health`)
- Scanner background task
- Agent registration with Nevermined

---

## 🔌 Extensibility

| What | How | Where |
|------|-----|-------|
| **New scorer sub-agent** | Write async function matching `Evaluator` protocol, call `pipeline.register()` | `evaluators/` + `main.py` startup |
| **New evaluation metrics** | Write any JSON to `evaluations.metrics` column | Your evaluator function |
| **New probe query strategy** | Pass custom `queries` list to `run_probe()` | Scanner or evaluator |
| **New discovery source** | Add to `scan_loop()` agent collection | `scanner.py` |
| **New consulting tool** | Add `@tool` to consulting agent | `consulting_agent.py` |
| **New payment rail** | `register_credits_plan()` with different `price_config` | `main.py` registration |

---

## 🛠️ Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| **Agent framework** | Strands Agents SDK | `@requires_payment` first-class, `invocation_state` passes x402 token natively |
| **LLM** | Claude Sonnet 4.6 via Bedrock | Inference profile, hackathon credits cover it |
| **Payment protocol** | x402 | Universal substrate — ~50 lines for full buy flow |
| **Payment SDK** | `payments-py[strands,a2a]` | Need `strands` for seller-side, `a2a` for discovery |
| **Web server** | FastAPI + Uvicorn on EC2 | Simplest path: `ssh`, `tmux`, `poetry run server` |
| **Persistence** | SQLite WAL | Single-process = perfect. Zero setup. Back up with cron to S3 |

---

## 📊 Implementation Phases

### Phase 1: Plumbing (must work end-to-end)

1. ✅ `central_sheet.py` — verify tables create, test CRUD
2. ✅ `buy_impl.py` — copy from starter kit, test against local seller
3. ✅ `probe_runner.py` — probe local seller, verify rows in probes table
4. ✅ `evaluation.py` + `evaluators/gate.py` — gate runs after probes
5. ✅ `scanner.py` — test with `agents_config.json` pointing at local seller
6. ✅ `main.py` — `/health`, `/portfolio`, scanner background task

**Milestone:** Scanner discovers seller → probes it → gate evaluator runs → portfolio shows data

### Phase 2: Sell

7. ✅ Agent registration with Nevermined
8. ✅ `consulting_agent.py` — read portfolio, answer questions
9. ✅ Wire `@requires_payment` on consulting endpoint
10. ✅ `/data` and `/pricing` endpoints live

**Milestone:** Another team can buy consulting from us via x402

### Phase 3: Score (your sub-agents)

11. 🚧 Add your scorer sub-agents to `evaluators/`
12. 🚧 Register them in `main.py` startup
13. 🚧 Each writes to `evaluations` table with its own `evaluator` name
14. 🚧 Portfolio logic reads evaluations to rank agents

**Milestone:** Portfolio has real quality + ROI rankings

### Phase 4: Polish

15. ⏳ Fan-out consulting: buy from top 2-3 agents, synthesize
16. ⏳ Dashboard endpoint with richer portfolio data
17. ⏳ AgentCore deployment config

---

## 🔐 Environment Variables

```bash
# Nevermined
NVM_API_KEY=nvm:...
NVM_AGENT_ID=                    # Auto-registered at startup
NVM_PLAN_ID=                     # Auto-registered at startup
NVM_PLAN_ID_USDC=                # Auto-registered at startup
NVM_PLAN_ID_FIAT=                # Auto-registered at startup
NVM_ENVIRONMENT=sandbox

# AWS / Bedrock
AWS_REGION=us-east-1
# AWS_ACCESS_KEY_ID=             # Not needed if using EC2 instance profile
# AWS_SECRET_ACCESS_KEY=         # Not needed if using EC2 instance profile

# App
PORT=3000
OUR_HOST=http://<your-ec2-public-ip>:3000
SCAN_INTERVAL=300                # Seconds between discovery scans
```

---

## 📁 Project Structure

```
portfolio-manager-agent/
├── pyproject.toml
├── .env.example
├── agents_config.json          # Fallback agent registry
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
│       └── quality_judge.py    # Placeholder: your scorer sub-agents
└── .bedrock_agentcore.yaml     # AgentCore deployment config
```

---

## 🎓 Key Patterns

### Buyer-side x402 purchase

```python
token_result = payments.x402.get_x402_access_token(
    plan_id=plan_id,
    agent_id=agent_id,
    token_options=token_options,
)
access_token = token_result.get("accessToken")

response = httpx.post(
    f"{seller_url}/data",
    headers={"payment-signature": access_token},
    json={"query": query},
)
```

### Seller-side payment enforcement

```python
@tool(context=True)
@requires_payment(
    payments=payments,
    plan_id=NVM_PLAN_ID,
    credits=1,
    agent_id=NVM_AGENT_ID,
)
def my_tool(query: str, tool_context=None) -> dict:
    # Decorator handles verify + settle automatically
    return {"status": "success", "content": [{"text": "..."}]}
```

### FastAPI endpoint passing payment token

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

---

## 📈 When to Upgrade

| Signal | Move to |
|--------|---------|
| Need HTTPS / custom domain | Put an ALB in front, or use Caddy as reverse proxy |
| Need >1 instance | ECS Fargate + RDS Postgres (swap SQLite for `asyncpg`) |
| Want zero-ops | AgentCore (`.bedrock_agentcore.yaml` already in repo) |

---

## 📝 License

MIT

---

## 🤝 Contributing

This is a hackathon project. If you want to extend it:

1. Add new evaluators to `src/evaluators/`
2. Register them in `main.py` startup
3. Each evaluator writes to the `evaluations` table with its own `evaluator` name
4. Portfolio logic automatically picks up new metrics

**The extensibility point is the `evaluations` table.** Schema-less JSON metrics column. Add a new scorer = add a new row with a new `evaluator` name. Existing code doesn't break.

---

Built with ⚡ for the Nevermined AI Agent Hackathon
