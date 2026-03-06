#!/bin/bash
# Deploy code and configure environment on EC2
# Usage: ./deploy-code.sh <public-ip> <key-file>

set -e

PUBLIC_IP="${1:-13.217.131.34}"
KEY_FILE="${2:-nevermind-agent-key.pem}"

echo "[ec2-deploy] Deploying code to $PUBLIC_IP..."

# Step 1: Clone repository
echo "[ec2-deploy] Cloning repository..."
ssh -i "$KEY_FILE" -o StrictHostKeyChecking=no ubuntu@"$PUBLIC_IP" << 'ENDSSH'
set -e
export PATH="$HOME/.local/bin:$PATH"

# Clone repo
if [ ! -d "nevermind-agent" ]; then
    git clone https://github.com/blueif16/nevermind-agent.git
fi

cd nevermind-agent
git pull origin main

# Install dependencies
poetry install

echo "✅ Code deployed and dependencies installed"
ENDSSH

# Step 2: Configure environment
echo "[ec2-deploy] Configuring environment..."

# Read local .env for NVM_API_KEY
if [ -f ".env" ]; then
    NVM_API_KEY=$(grep "^NVM_API_KEY=" .env | cut -d'=' -f2)
    NVM_ENVIRONMENT=$(grep "^NVM_ENVIRONMENT=" .env | cut -d'=' -f2 || echo "sandbox")
else
    echo "Warning: No local .env file found. You'll need to configure manually."
    NVM_API_KEY=""
    NVM_ENVIRONMENT="sandbox"
fi

ssh -i "$KEY_FILE" ubuntu@"$PUBLIC_IP" << ENDSSH
set -e
export PATH="\$HOME/.local/bin:\$PATH"
cd nevermind-agent

# Create .env file
cp .env.example .env

# Configure with public IP
cat > .env << EOF
# ── Nevermined ──────────────────────────────────────────────
NVM_API_KEY=${NVM_API_KEY}
NVM_ENVIRONMENT=${NVM_ENVIRONMENT}
NVM_AGENT_ID=
NVM_PLAN_ID=
NVM_PLAN_ID_USDC=
NVM_PLAN_ID_FIAT=

# ── AWS / Bedrock ───────────────────────────────────────────
# Using EC2 instance profile - no keys needed
AWS_REGION=us-east-1

# ── App ─────────────────────────────────────────────────────
PORT=3000
OUR_HOST=http://${PUBLIC_IP}:3000
SCAN_INTERVAL=300
SELLER_URL=http://localhost:3000
EOF

echo "✅ Environment configured"
ENDSSH

# Step 3: Verify installation
echo "[ec2-deploy] Verifying installation..."
ssh -i "$KEY_FILE" ubuntu@"$PUBLIC_IP" << 'ENDSSH'
set -e
export PATH="$HOME/.local/bin:$PATH"
cd nevermind-agent
poetry run python -c "from src.main import app; print('✅ Server imports successfully')"
ENDSSH

echo ""
echo "[ec2-deploy] ✅ Code deployment complete!"
echo ""
echo "Next step: Start the server"
echo "  ssh -i $KEY_FILE ubuntu@$PUBLIC_IP"
echo "  cd nevermind-agent"
echo "  tmux new -s agent"
echo "  poetry run server"
