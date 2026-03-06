#!/usr/bin/env python3
"""
Portfolio Manager — Ops Toolkit
Run on EC2: python3 ops.py <command>

Commands:
  status     — Full system status: agents, probes, errors, P&L
  errors     — Show probe error breakdown (find out WHY agents are dead)
  alive      — Probe /pricing on all discovered agents (no x402 needed)
  cleanup    — Remove duplicate/empty agents, reset dead→new for retry
  test-buy   — Test buying from a specific seller (needs plan ordered)
  reset      — Nuclear option: delete DB and let scanner rediscover
"""

import sys
import os
import json
import sqlite3
import httpx
import time

DB_PATH = os.environ.get("DB_PATH", "portfolio.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def cmd_status():
    """Full system status."""
    db = get_db()

    agents = db.execute("SELECT * FROM agents").fetchall()
    probes = db.execute("SELECT * FROM probes").fetchall()
    evals = db.execute("SELECT * FROM evaluations").fetchall()

    status_counts = {}
    for a in agents:
        s = a["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    success_probes = [p for p in probes if p["error"] is None]
    error_probes = [p for p in probes if p["error"] is not None]

    pnl = db.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN direction='in' THEN credits END), 0) as revenue,
            COALESCE(SUM(CASE WHEN direction='out' THEN credits END), 0) as spent
        FROM ledger
    """).fetchone()

    print("=" * 60)
    print("PORTFOLIO MANAGER — STATUS REPORT")
    print("=" * 60)
    print(f"\nAgents: {len(agents)} total")
    for s, c in sorted(status_counts.items()):
        print(f"  {s:12s}: {c}")
    print(f"\nProbes: {len(probes)} total")
    print(f"  Success: {len(success_probes)}")
    print(f"  Errors:  {len(error_probes)}")
    print(f"\nEvaluations: {len(evals)}")
    print(f"\nP&L: revenue={pnl['revenue']}, spent={pnl['spent']}, margin={pnl['revenue'] - pnl['spent']}")

    # Show agents with their URLs
    print(f"\n{'─' * 60}")
    print(f"{'Status':10s} {'Name':30s} {'URL':40s}")
    print(f"{'─' * 60}")
    for a in agents:
        name = (a["name"] or "unnamed")[:30]
        url = (a["url"] or "no url")[:40]
        print(f"{a['status']:10s} {name:30s} {url}")

    db.close()


def cmd_errors():
    """Show probe error breakdown."""
    db = get_db()

    rows = db.execute("""
        SELECT
            a.name,
            a.url,
            p.error,
            COUNT(*) as count
        FROM probes p
        JOIN agents a ON p.agent_id = a.agent_id
        WHERE p.error IS NOT NULL
        GROUP BY a.name, p.error
        ORDER BY count DESC
        LIMIT 50
    """).fetchall()

    if not rows:
        print("No probe errors found. Either no probes ran or all succeeded.")
        return

    print(f"{'Count':6s} {'Agent':30s} {'Error (truncated)':60s}")
    print("─" * 100)
    for r in rows:
        name = (r["name"] or "unnamed")[:30]
        error = (r["error"] or "")[:60]
        print(f"{r['count']:6d} {name:30s} {error}")

    # Summary: group errors by type
    print(f"\n{'─' * 60}")
    print("ERROR TYPE SUMMARY:")
    type_counts = {}
    all_errors = db.execute("SELECT error FROM probes WHERE error IS NOT NULL").fetchall()
    for e in all_errors:
        err = e["error"] or ""
        if "access token" in err.lower() or "token" in err.lower():
            t = "TOKEN/SUBSCRIPTION"
        elif "connect" in err.lower() or "connection" in err.lower():
            t = "CONNECTION REFUSED"
        elif "html" in err.lower() or "<!doctype" in err.lower() or "<html" in err.lower():
            t = "HTML RESPONSE (wrong endpoint)"
        elif "402" in err or "payment" in err.lower():
            t = "PAYMENT REQUIRED (402)"
        elif "500" in err:
            t = "SERVER ERROR (500)"
        elif "404" in err:
            t = "NOT FOUND (404)"
        elif "timeout" in err.lower():
            t = "TIMEOUT"
        else:
            t = "OTHER"
        type_counts[t] = type_counts.get(t, 0) + 1

    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {c:4d}  {t}")

    db.close()


def cmd_alive():
    """Probe /pricing on all discovered agents (no x402 needed)."""
    db = get_db()
    agents = db.execute("SELECT agent_id, name, url FROM agents WHERE url != ''").fetchall()
    db.close()

    print(f"Probing /pricing for {len(agents)} agents...\n")
    print(f"{'Status':8s} {'Latency':10s} {'Name':30s} {'URL':50s}")
    print("─" * 100)

    alive = []
    for a in agents:
        url = a["url"].rstrip("/")
        try:
            t0 = time.time()
            resp = httpx.get(f"{url}/pricing", timeout=8.0)
            latency = (time.time() - t0) * 1000
            status = resp.status_code

            name = (a["name"] or "unnamed")[:30]
            if status == 200:
                print(f"{'✅ LIVE':8s} {latency:7.0f}ms  {name:30s} {url}")
                alive.append(a)
            else:
                print(f"{'❌ ' + str(status):8s} {latency:7.0f}ms  {name:30s} {url}")
        except Exception as e:
            name = (a["name"] or "unnamed")[:30]
            err_type = type(e).__name__
            print(f"{'💀 DOWN':8s} {'—':10s} {name:30s} {url}  ({err_type})")

    print(f"\n{'─' * 60}")
    print(f"ALIVE: {len(alive)} / {len(agents)} agents respond to /pricing")
    if alive:
        print("\nAlive agents you should ORDER PLANS for:")
        for a in alive:
            print(f"  - {a['name']} → {a['url']}/pricing")


def cmd_cleanup():
    """Remove duplicates and reset dead agents for retry."""
    db = get_db()

    # Count before
    total_before = db.execute("SELECT COUNT(*) FROM agents").fetchone()[0]

    # Find agents with no URL or no plan_id (useless)
    useless = db.execute(
        "SELECT agent_id, name FROM agents WHERE url = '' OR url IS NULL"
    ).fetchall()

    if useless:
        print(f"Removing {len(useless)} agents with no URL:")
        for a in useless:
            print(f"  - {a['name'] or a['agent_id'][:30]}")
            db.execute("DELETE FROM probes WHERE agent_id = ?", (a["agent_id"],))
            db.execute("DELETE FROM evaluations WHERE agent_id = ?", (a["agent_id"],))
            db.execute("DELETE FROM agents WHERE agent_id = ?", (a["agent_id"],))

    # Find agents with no plan_id (can't probe them)
    no_plan = db.execute(
        "SELECT agent_id, name, url FROM agents WHERE plan_id = '' OR plan_id IS NULL"
    ).fetchall()
    if no_plan:
        print(f"\nRemoving {len(no_plan)} agents with no plan_id (can't probe):")
        for a in no_plan:
            print(f"  - {a['name'] or 'unnamed'} ({a['url'][:40]})")
            db.execute("DELETE FROM probes WHERE agent_id = ?", (a["agent_id"],))
            db.execute("DELETE FROM evaluations WHERE agent_id = ?", (a["agent_id"],))
            db.execute("DELETE FROM agents WHERE agent_id = ?", (a["agent_id"],))

    # Reset dead agents that had TOKEN errors (might work after ordering plans)
    token_dead = db.execute("""
        SELECT DISTINCT a.agent_id, a.name
        FROM agents a
        JOIN probes p ON a.agent_id = p.agent_id
        WHERE a.status = 'dead'
          AND (p.error LIKE '%token%' OR p.error LIKE '%Token%'
               OR p.error LIKE '%access%' OR p.error LIKE '%402%'
               OR p.error LIKE '%payment%')
    """).fetchall()

    if token_dead:
        print(f"\nResetting {len(token_dead)} 'dead' agents that failed on token/payment (retry after ordering plans):")
        for a in token_dead:
            print(f"  - {a['name'] or a['agent_id'][:30]} → status: new")
            db.execute("UPDATE agents SET status = 'new' WHERE agent_id = ?", (a["agent_id"],))

    db.commit()

    total_after = db.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    print(f"\nCleanup done: {total_before} → {total_after} agents")

    db.close()


def cmd_test_buy(seller_url=None, plan_id=None, query=None):
    """Test buying from a specific seller."""
    if not seller_url:
        print("Usage: python3 ops.py test-buy <seller_url> <plan_id> [query]")
        print("Example: python3 ops.py test-buy https://example.com did:nv:abc123 'What services do you offer?'")
        return

    query = query or "What services do you provide?"

    # Import from your codebase
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from dotenv import load_dotenv
    load_dotenv()

    from payments_py import Payments, PaymentOptions
    from src.buy_impl import purchase_data_impl

    nvm_api_key = os.environ["NVM_API_KEY"]
    nvm_env = os.environ.get("NVM_ENVIRONMENT", "sandbox")

    payments = Payments.get_instance(
        PaymentOptions(nvm_api_key=nvm_api_key, environment=nvm_env)
    )

    print(f"Testing purchase from: {seller_url}")
    print(f"Plan ID: {plan_id}")
    print(f"Query: {query}")
    print("─" * 60)

    t0 = time.time()
    result = purchase_data_impl(
        payments=payments,
        plan_id=plan_id,
        seller_url=seller_url,
        query=query,
    )
    elapsed = (time.time() - t0) * 1000

    print(f"\nResult ({elapsed:.0f}ms):")
    print(json.dumps(result, indent=2, default=str))


def cmd_reset():
    """Nuclear: delete DB."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print(f"Deleted {DB_PATH}. Restart server to rediscover all agents.")
    else:
        print(f"{DB_PATH} not found.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        cmd_status()
    elif cmd == "errors":
        cmd_errors()
    elif cmd == "alive":
        cmd_alive()
    elif cmd == "cleanup":
        cmd_cleanup()
    elif cmd == "test-buy":
        url = sys.argv[2] if len(sys.argv) > 2 else None
        pid = sys.argv[3] if len(sys.argv) > 3 else None
        q = sys.argv[4] if len(sys.argv) > 4 else None
        cmd_test_buy(url, pid, q)
    elif cmd == "reset":
        cmd_reset()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
