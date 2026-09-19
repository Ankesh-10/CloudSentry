#!/bin/bash
# scripts/shutdown_demo.sh
# Terminates the demo environment resources.

ENDPOINT_ARG=""
if [ -n "$AWS_ENDPOINT_URL" ]; then
    ENDPOINT_ARG="--endpoint-url $AWS_ENDPOINT_URL"
    echo "🔧 Using Mock AWS Endpoint: $AWS_ENDPOINT_URL"
fi

if [ ! -f .demo_instances ]; then
    echo "❌ No .demo_instances file found. Nothing to terminate."
    exit 1
fi

INSTANCE_IDS=$(cat .demo_instances)

echo "🗑️ Terminating Demo EC2 Instances: $INSTANCE_IDS"
aws ec2 terminate-instances --instance-ids $INSTANCE_IDS $ENDPOINT_ARG > /dev/null

echo "🗑️ Deleting Demo Lambda Function..."
aws lambda delete-function --function-name CloudSentry-Demo-Lambda $ENDPOINT_ARG > /dev/null 2>&1 || true

rm .demo_instances
echo "🎉 Demo environment terminated."
