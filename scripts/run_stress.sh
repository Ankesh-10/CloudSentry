#!/bin/bash
# scripts/run_stress.sh
# Pushes fake high-CPU datapoints so the anomaly detector flags a spike.
#
# MOCK AWS ONLY (Moto/LocalStack with AWS_ENDPOINT_URL set): real CloudWatch
# rejects PutMetricData into the reserved AWS/* namespaces. On real AWS, put
# load on the instance instead (e.g. `stress-ng --cpu 2 --timeout 20m`), or use
# scripts/inject_anomaly.py against a non-production database.

set -euo pipefail

if [ -z "${AWS_ENDPOINT_URL:-}" ]; then
    echo "❌ run_stress.sh only works against a mock endpoint (set AWS_ENDPOINT_URL)." >&2
    echo "   Real CloudWatch does not accept custom data in the AWS/EC2 namespace." >&2
    exit 1
fi
ENDPOINT_ARG="--endpoint-url $AWS_ENDPOINT_URL"
echo "🔧 Using Mock AWS Endpoint: $AWS_ENDPOINT_URL"

if [ ! -f .demo_instances ]; then
    echo "❌ No .demo_instances file found. Run scripts/provision_demo.sh first."
    exit 1
fi

INSTANCE_IDS=$(cat .demo_instances)

echo "🔥 Simulating CPU Stress Spike (Anomaly) for demonstration..."

for INSTANCE_ID in $INSTANCE_IDS; do
    echo "Pushing 99% CPU Utilization for $INSTANCE_ID..."

    # Push 3 data points, 5 minutes apart to simulate a sustained spike
    for i in 0 1 2; do
        if date --version >/dev/null 2>&1; then
            TIMESTAMP=$(date -u -d "$((i * 5)) minutes ago" +%Y-%m-%dT%H:%M:%SZ)
        else
            TIMESTAMP=$(date -u -v-$((i * 5))M +%Y-%m-%dT%H:%M:%SZ)
        fi

        # shellcheck disable=SC2086
        aws cloudwatch put-metric-data \
            --namespace AWS/EC2 \
            --metric-name CPUUtilization \
            --dimensions Name=InstanceId,Value="$INSTANCE_ID" \
            --value 99.0 \
            --unit Percent \
            --timestamp "$TIMESTAMP" \
            $ENDPOINT_ARG
    done
done

echo "✅ Stress metrics pushed to the mock CloudWatch."
echo "CloudSentry's AnomalyDetectorService should flag this during the next cycle."
