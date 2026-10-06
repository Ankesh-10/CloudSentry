#!/bin/bash
# scripts/provision_demo.sh
# Provisions a small set of AWS resources for a CloudSentry demo: one EC2
# instance and one Lambda function. Use a sandbox account, never production.
#
# Real AWS needs an existing Lambda execution role:
#   DEMO_LAMBDA_ROLE_ARN=arn:aws:iam::<account>:role/<role> scripts/provision_demo.sh
# (terraform/ creates one: `terraform output demo_lambda_role_arn`.)
# Mock AWS (Moto/LocalStack): set AWS_ENDPOINT_URL; a dummy role is used.

set -euo pipefail

ENDPOINT_ARG=""
if [ -n "${AWS_ENDPOINT_URL:-}" ]; then
    ENDPOINT_ARG="--endpoint-url $AWS_ENDPOINT_URL"
    echo "🔧 Using Mock AWS Endpoint: $AWS_ENDPOINT_URL"
    ROLE_ARN="${DEMO_LAMBDA_ROLE_ARN:-arn:aws:iam::123456789012:role/dummy-role}"
else
    ROLE_ARN="${DEMO_LAMBDA_ROLE_ARN:?Set DEMO_LAMBDA_ROLE_ARN to an existing Lambda execution role}"
fi

echo "🚀 Provisioning CloudSentry Demo Environment..."

# 1. One t3.micro instance. Real AWS: latest Amazon Linux 2023 (AL2 is EOL).
if [ -n "${AWS_ENDPOINT_URL:-}" ]; then
    AMI_ID="ami-12c6146b"
else
    AMI_ID=$(aws ssm get-parameter \
        --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
        --query 'Parameter.Value' --output text)
fi

# shellcheck disable=SC2086
INSTANCE_IDS=$(aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --count 1 \
    --instance-type t3.micro \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=CloudSentry-Demo-EC2},{Key=Environment,Value=Demo},{Key=ManagedBy,Value=CloudSentry}]' \
    --query 'Instances[*].InstanceId' \
    --output text $ENDPOINT_ARG)

echo "✅ Provisioned EC2 Instances: $INSTANCE_IDS"
echo "$INSTANCE_IDS" > .demo_instances

# 2. A minimal Lambda function for discovery and runaway-Lambda demos.
echo "✅ Provisioning Lambda Function: CloudSentry-Demo-Lambda"
WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT
echo 'def handler(event, context): return "Hello"' > "$WORKDIR/dummy_lambda.py"
(cd "$WORKDIR" && zip -q dummy_lambda.zip dummy_lambda.py)

# shellcheck disable=SC2086
if ! aws lambda create-function \
    --function-name CloudSentry-Demo-Lambda \
    --runtime python3.12 \
    --role "$ROLE_ARN" \
    --handler dummy_lambda.handler \
    --zip-file "fileb://$WORKDIR/dummy_lambda.zip" \
    $ENDPOINT_ARG > /dev/null 2>"$WORKDIR/err.txt"; then
    if grep -q ResourceConflictException "$WORKDIR/err.txt"; then
        echo "ℹ️ Lambda CloudSentry-Demo-Lambda already exists; keeping it."
    else
        echo "❌ Lambda creation failed:" >&2
        cat "$WORKDIR/err.txt" >&2
        exit 1
    fi
fi

echo "🎉 Demo environment provisioned successfully!"
