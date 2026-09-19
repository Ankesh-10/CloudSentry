#!/bin/bash
# scripts/provision_demo.sh
# Provisions a set of AWS resources for CloudSentry demo purposes.
# Supports overriding ENDPOINT_URL for local mock environments (e.g., Moto or LocalStack).

set -e

ENDPOINT_ARG=""
if [ -n "$AWS_ENDPOINT_URL" ]; then
    ENDPOINT_ARG="--endpoint-url $AWS_ENDPOINT_URL"
    echo "🔧 Using Mock AWS Endpoint: $AWS_ENDPOINT_URL"
fi

echo "🚀 Provisioning CloudSentry Demo Environment..."

# 1. Spin up 3 EC2 instances (t2.micro)
# Using a dummy AMI for mock environments, or falling back to an Amazon Linux AMI format
AMI_ID="ami-12c6146b"

INSTANCE_IDS=$(aws ec2 run-instances \
    --image-id $AMI_ID \
    --count 3 \
    --instance-type t2.micro \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=CloudSentry-Demo-EC2},{Key=Environment,Value=Demo}]' \
    --query 'Instances[*].InstanceId' \
    --output text $ENDPOINT_ARG)

echo "✅ Provisioned EC2 Instances: $INSTANCE_IDS"
echo $INSTANCE_IDS > .demo_instances

# 2. Create a Lambda function (Dummy creation for discovery)
echo "✅ Provisioning Lambda Function: CloudSentry-Demo-Lambda"

# Create a dummy zip file for Lambda
echo 'def handler(event, context): return "Hello"' > dummy_lambda.py
zip dummy_lambda.zip dummy_lambda.py > /dev/null

aws lambda create-function \
    --function-name CloudSentry-Demo-Lambda \
    --runtime python3.9 \
    --role arn:aws:iam::123456789012:role/dummy-role \
    --handler dummy_lambda.handler \
    --zip-file fileb://dummy_lambda.zip \
    $ENDPOINT_ARG > /dev/null 2>&1 || echo "⚠️ Lambda creation skipped or already exists."

rm dummy_lambda.py dummy_lambda.zip

echo "🎉 Demo environment provisioned successfully!"
