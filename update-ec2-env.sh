#!/bin/bash
# Update EC2 .env with correct NVM IDs to prevent duplicate marketplace entries
# Usage: ./update-ec2-env.sh <ec2-ip>

set -e

EC2_IP="${1}"
KEY_FILE="nevermind-agent-key.pem"

if [ -z "$EC2_IP" ]; then
    echo "Usage: ./update-ec2-env.sh <ec2-ip>"
    echo "Example: ./update-ec2-env.sh 54.165.73.210"
    exit 1
fi

echo "Updating .env on EC2 instance $EC2_IP..."

ssh -i "$KEY_FILE" ubuntu@"$EC2_IP" << 'ENDSSH'
cd ~/nevermind-agent

# Backup current .env
cp .env .env.backup

# Update the three critical IDs
sed -i 's/^NVM_AGENT_ID=.*/NVM_AGENT_ID=43254222956824542851305011350795028015667700755757138077142648108953221742011/' .env
sed -i 's/^NVM_PLAN_ID=.*/NVM_PLAN_ID=32264978581521060596226612106593412508601533951706321127584597160009188094462/' .env
sed -i 's/^NVM_PLAN_ID_USDC=.*/NVM_PLAN_ID_USDC=32264978581521060596226612106593412508601533951706321127584597160009188094462/' .env

echo "✅ Updated .env with correct NVM IDs"
echo ""
echo "Changes:"
grep "^NVM_AGENT_ID=" .env
grep "^NVM_PLAN_ID=" .env
grep "^NVM_PLAN_ID_USDC=" .env
ENDSSH

echo ""
echo "✅ EC2 .env updated successfully!"
echo ""
echo "Next steps:"
echo "1. SSH to EC2: ssh -i $KEY_FILE ubuntu@$EC2_IP"
echo "2. Restart the server:"
echo "   tmux attach -t agent"
echo "   Ctrl+C to stop"
echo "   poetry run server"
echo ""
echo "3. Delete the 4 duplicate marketplace entries via Nevermined dashboard UI"
echo "   Keep only the one marked 'Marketplace Ready'"
