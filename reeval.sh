#!/bin/bash
# Trigger re-evaluation of agents with updated quality_judge logic

set -e

echo "🔄 Re-evaluation Pipeline"
echo "========================="

# Pull latest code
echo "📥 Pulling latest code..."
git pull origin main

# Clear stale quality_judge evaluations
echo "🗑️  Clearing old quality_judge evaluations..."
sqlite3 portfolio.db "DELETE FROM evaluations WHERE evaluator='quality_judge';"

# Mark evaluated agents for re-evaluation
echo "🔄 Marking agents for re-evaluation..."
UPDATED=$(sqlite3 portfolio.db "UPDATE agents SET status='reeval' WHERE status='evaluated'; SELECT changes();")
echo "   → $UPDATED agents marked for reeval"

# Find and restart the server process
echo "🔄 Restarting server..."
PID=$(ps aux | grep '[p]ython.*src.main' | awk '{print $2}')
if [ -n "$PID" ]; then
    kill $PID
    echo "   → Killed old process (PID: $PID)"
    sleep 2
fi

# Start server in background
nohup /home/ubuntu/.cache/pypoetry/virtualenvs/portfolio-manager-agent-a5CkJlXQ-py3.12/bin/python -m src.main > server.log 2>&1 &
NEW_PID=$!
echo "   → Started new process (PID: $NEW_PID)"

sleep 3

# Verify server is running
if curl -s http://localhost:3000/health > /dev/null; then
    echo "✅ Server is running"
    echo ""
    echo "Next steps:"
    echo "  - Scanner will re-probe agents on next cycle"
    echo "  - New quality_judge scores will be calculated"
    echo "  - Check dashboard: http://13.217.131.34:3000/dashboard"
else
    echo "❌ Server failed to start. Check server.log"
    exit 1
fi
