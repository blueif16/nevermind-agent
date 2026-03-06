# EC2 Deployment
Branch: main | Level: 2 | Type: implement | Status: complete
Started: 2026-03-05T17:30:00Z
Completed: 2026-03-06T01:45:00Z

## DAG
```mermaid
graph LR
    T1["✅ T1: Provision EC2"] --> T2["✅ T2: Deploy code"]
    T2 --> T3["✅ T3: Start server"]
    T3 --> T4["✅ T4: Verify endpoints"]
    style T1 fill:#22c55e,color:#000
    style T2 fill:#22c55e,color:#000
    style T3 fill:#22c55e,color:#000
    style T4 fill:#22c55e,color:#000
```

## Tree
```
✅ T1: Provision EC2 instance [careful]
└──→ ✅ T2: Deploy code and configure environment [routine]
     └──→ ✅ T3: Start server and verify registration [careful]
          └──→ ✅ T4: Verify all endpoints are functional [routine]
```

## Tasks

### T1: Provision EC2 instance with Python and Poetry [implement] [careful]
- Scope: AWS EC2 console/CLI
- Verify: `ssh -i key.pem ubuntu@<ip> "python3.12 --version && poetry --version"`
- Needs: none
- Status: done ✅ (15m)
- Summary: Launched t3.medium instance (i-0a1997f3ca9e0aca3), installed Python 3.12 and Poetry, configured security groups and IAM role
- Files: deploy-ec2.sh
- Instance: i-0a1997f3ca9e0aca3, IP: 13.217.131.34

### T2: Deploy code and configure environment [implement] [routine]
- Scope: EC2 instance filesystem, .env configuration
- Verify: `ssh -i key.pem ubuntu@<ip> "cd portfolio-manager-agent && poetry run python -c 'from src.main import app; print(\"OK\")'"`
- Needs: T1
- Status: done ✅ (8m)
- Summary: Cloned repo, installed dependencies with Poetry, configured .env with NVM_API_KEY and public IP
- Files: deploy-code.sh, .env on EC2

### T3: Start server and verify registration [implement] [careful]
- Scope: Server process, Nevermined registration
- Verify: `curl http://<public-ip>:3000/health`
- Needs: T2
- Status: done ✅ (12m)
- Summary: Started server in tmux, registered agent with Nevermined (USDC plan), fixed IAM permissions for Bedrock access
- Agent ID: 43254222956824542851305011350795028015667700755757138077142648108953221742011
- Plan ID: 32264978581521060596226612106593412508601533951706321127584597160009188094462

### T4: Verify all endpoints are functional [test] [routine]
- Scope: API endpoints
- Verify: `curl http://<public-ip>:3000/health && curl http://<public-ip>:3000/pricing && curl http://<public-ip>:3000/portfolio`
- Needs: T3
- Status: done ✅ (5m)
- Summary: All endpoints verified - /health, /pricing, /portfolio, /data (returns 402 with consulting response as expected)
- Consulting agent successfully responds with Claude Sonnet 4.6 via Bedrock

## Summary
Completed: 4/4 | Duration: ~40m
Files changed: deploy-ec2.sh, deploy-code.sh, IAM policy
All verifications: passed

**Deployment Details:**
- Instance: i-0a1997f3ca9e0aca3
- Public IP: http://13.217.131.34:3000
- Agent ID: 43254222956824542851305011350795028015667700755757138077142648108953221742011
- USDC Plan ID: 32264978581521060596226612106593412508601533951706321127584597160009188094462
- Key: nevermind-agent-key.pem

**Endpoints:**
- GET /health - ✅ Returns agent stats and P&L
- GET /pricing - ✅ Returns USDC payment plan
- GET /portfolio - ✅ Returns empty portfolio (scanner running)
- POST /data - ✅ Returns 402 with consulting response (payment required)

**Server Status:**
- Running in tmux session 'agent'
- Scanner active (300s interval)
- Evaluators registered: gate, quality_judge
- Bedrock model: Claude Sonnet 4.6 (us-east-1)

**Access:**
```bash
ssh -i nevermind-agent-key.pem ubuntu@13.217.131.34
tmux attach -t agent  # View server logs
```
