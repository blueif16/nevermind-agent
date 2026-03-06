#!/bin/bash
# EC2 Deployment Script for Consulting Agent
# Run this locally with your AWS credentials configured

set -e

echo "[ec2-deploy] Starting EC2 provisioning..."

# Configuration
INSTANCE_TYPE="t3.medium"
AMI_ID="ami-0e2c8caa4b6378d8c"  # Ubuntu 24.04 LTS in us-east-1
KEY_NAME="${KEY_NAME:-nevermind-agent-key}"
SECURITY_GROUP_NAME="nevermind-agent-sg"
IAM_ROLE_NAME="nevermind-agent-bedrock-role"
REGION="${AWS_REGION:-us-east-1}"

echo "[ec2-deploy] Using region: $REGION"

# Step 1: Create key pair if it doesn't exist
if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" &>/dev/null; then
    echo "[ec2-deploy] Creating key pair: $KEY_NAME"
    aws ec2 create-key-pair \
        --key-name "$KEY_NAME" \
        --region "$REGION" \
        --query 'KeyMaterial' \
        --output text > "${KEY_NAME}.pem"
    chmod 400 "${KEY_NAME}.pem"
    echo "[ec2-deploy] Key pair saved to ${KEY_NAME}.pem"
else
    echo "[ec2-deploy] Key pair $KEY_NAME already exists"
fi

# Step 2: Create security group if it doesn't exist
SG_ID=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$SECURITY_GROUP_NAME" \
    --region "$REGION" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || echo "None")

if [ "$SG_ID" = "None" ]; then
    echo "[ec2-deploy] Creating security group: $SECURITY_GROUP_NAME"
    SG_ID=$(aws ec2 create-security-group \
        --group-name "$SECURITY_GROUP_NAME" \
        --description "Security group for Nevermind consulting agent" \
        --region "$REGION" \
        --query 'GroupId' \
        --output text)

    # Allow SSH (port 22)
    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 22 \
        --cidr 0.0.0.0/0 \
        --region "$REGION"

    # Allow HTTP on port 3000
    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 3000 \
        --cidr 0.0.0.0/0 \
        --region "$REGION"

    echo "[ec2-deploy] Security group created: $SG_ID"
else
    echo "[ec2-deploy] Security group already exists: $SG_ID"
fi

# Step 3: Create IAM role for Bedrock access
if ! aws iam get-role --role-name "$IAM_ROLE_NAME" --region "$REGION" &>/dev/null; then
    echo "[ec2-deploy] Creating IAM role: $IAM_ROLE_NAME"

    # Trust policy for EC2
    cat > /tmp/trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "ec2.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

    aws iam create-role \
        --role-name "$IAM_ROLE_NAME" \
        --assume-role-policy-document file:///tmp/trust-policy.json \
        --region "$REGION"

    # Bedrock permissions policy
    cat > /tmp/bedrock-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:ListInferenceProfiles"
      ],
      "Resource": "arn:aws:bedrock:us-east-1:*:inference-profile/us.anthropic.claude-sonnet-4-6"
    }
  ]
}
EOF

    aws iam put-role-policy \
        --role-name "$IAM_ROLE_NAME" \
        --policy-name "BedrockAccess" \
        --policy-document file:///tmp/bedrock-policy.json \
        --region "$REGION"

    # Create instance profile
    aws iam create-instance-profile \
        --instance-profile-name "$IAM_ROLE_NAME" \
        --region "$REGION"

    aws iam add-role-to-instance-profile \
        --instance-profile-name "$IAM_ROLE_NAME" \
        --role-name "$IAM_ROLE_NAME" \
        --region "$REGION"

    echo "[ec2-deploy] Waiting 10s for IAM role to propagate..."
    sleep 10
else
    echo "[ec2-deploy] IAM role already exists: $IAM_ROLE_NAME"
fi

# Step 4: Launch EC2 instance
echo "[ec2-deploy] Launching EC2 instance..."

INSTANCE_ID=$(aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --iam-instance-profile "Name=$IAM_ROLE_NAME" \
    --region "$REGION" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=nevermind-consulting-agent}]" \
    --query 'Instances[0].InstanceId' \
    --output text)

echo "[ec2-deploy] Instance launched: $INSTANCE_ID"
echo "[ec2-deploy] Waiting for instance to be running..."

aws ec2 wait instance-running \
    --instance-ids "$INSTANCE_ID" \
    --region "$REGION"

# Get public IP
PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --region "$REGION" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)

echo "[ec2-deploy] Instance is running!"
echo "[ec2-deploy] Public IP: $PUBLIC_IP"
echo "[ec2-deploy] Waiting 30s for SSH to be ready..."
sleep 30

# Step 5: Install dependencies
echo "[ec2-deploy] Installing Python 3.12, pipx, and Poetry..."

ssh -i "${KEY_NAME}.pem" -o StrictHostKeyChecking=no ubuntu@"$PUBLIC_IP" << 'ENDSSH'
set -e
sudo apt update
sudo apt install -y python3.12 python3.12-venv pipx git
pipx install poetry
pipx ensurepath
export PATH="$HOME/.local/bin:$PATH"
python3.12 --version
poetry --version
ENDSSH

echo "[ec2-deploy] ✅ EC2 instance provisioned successfully!"
echo ""
echo "Instance Details:"
echo "  Instance ID: $INSTANCE_ID"
echo "  Public IP: $PUBLIC_IP"
echo "  Key: ${KEY_NAME}.pem"
echo "  Region: $REGION"
echo ""
echo "Verification command:"
echo "  ssh -i ${KEY_NAME}.pem ubuntu@$PUBLIC_IP \"python3.12 --version && poetry --version\""
echo ""
echo "Save these for next steps:"
echo "  export EC2_IP=$PUBLIC_IP"
echo "  export EC2_KEY=${KEY_NAME}.pem"
