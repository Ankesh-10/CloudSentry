#!/bin/bash
# scripts/shutdown_demo.sh
# Stops (default) or terminates (--terminate) the demo resources created by
# provision_demo.sh, and deletes the demo Lambda.
#
#   scripts/shutdown_demo.sh              # stop instances; keep .demo_instances
#   scripts/shutdown_demo.sh --terminate  # terminate instances; remove .demo_instances
#
# Stopped instances stop compute billing but their EBS volumes still bill, and
# the ids stay in .demo_instances so they can be terminated later.

set -euo pipefail

MODE="stop"
if [ "${1:-}" = "--terminate" ]; then
    MODE="terminate"
fi

ENDPOINT_ARG=""
if [ -n "${AWS_ENDPOINT_URL:-}" ]; then
    ENDPOINT_ARG="--endpoint-url $AWS_ENDPOINT_URL"
    echo "🔧 Using Mock AWS Endpoint: $AWS_ENDPOINT_URL"
fi

if [ ! -f .demo_instances ]; then
    echo "❌ No .demo_instances file found. Nothing to do."
    exit 1
fi

INSTANCE_IDS=$(cat .demo_instances)

if [ "$MODE" = "terminate" ]; then
    echo "🗑️ Terminating demo EC2 instances: $INSTANCE_IDS"
    # shellcheck disable=SC2086
    aws ec2 terminate-instances --instance-ids $INSTANCE_IDS $ENDPOINT_ARG > /dev/null
else
    echo "⏸️ Stopping demo EC2 instances: $INSTANCE_IDS"
    # shellcheck disable=SC2086
    aws ec2 stop-instances --instance-ids $INSTANCE_IDS $ENDPOINT_ARG > /dev/null
fi

echo "🗑️ Deleting demo Lambda function..."
# shellcheck disable=SC2086
if ! aws lambda delete-function --function-name CloudSentry-Demo-Lambda $ENDPOINT_ARG > /dev/null 2>lambda_err.txt; then
    if grep -q ResourceNotFoundException lambda_err.txt; then
        echo "ℹ️ Demo Lambda not found (already deleted or never created)."
    else
        cat lambda_err.txt >&2
        rm -f lambda_err.txt
        exit 1
    fi
fi
rm -f lambda_err.txt

if [ "$MODE" = "terminate" ]; then
    rm .demo_instances
    echo "🎉 Demo environment terminated."
else
    echo "🎉 Demo instances stopped. Run with --terminate to delete them."
fi
