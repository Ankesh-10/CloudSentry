#!/bin/bash
# scripts/run_stress.sh
# Pushes high CPU metrics to CloudWatch to trigger the Anomaly Detector

ENDPOINT_ARG=""
if [ -n "$AWS_ENDPOINT_URL" ]; then
    ENDPOINT_ARG="--endpoint-url $AWS_ENDPOINT_URL"
    echo "🔧 Using Mock AWS Endpoint: $AWS_ENDPOINT_URL"
fi

if [ ! -f .demo_instances ]; then
    echo "❌ No .demo_instances file found. Run scripts/provision_demo.sh first."
    exit 1
fi

INSTANCE_IDS=$(cat .demo_instances)

echo "🔥 Simulating CPU Stress Spike (Anomaly) for demonstration..."

for INSTANCE_ID in $INSTANCE_IDS; do
    echo "Pushing 99% CPU Utilization for $INSTANCE_ID..."
    
    # Push 3 data points, 5 minutes apart to simulate a sustained spike
    for i in {0..2}; do
        # Calculate timestamp for macOS or Linux
        if date --version >/dev/null 2>&1; then
            TIMESTAMP=$(date -u -d "$((i * 5)) minutes ago" +%Y-%m-%dT%H:%M:%SZ)
        else
            TIMESTAMP=$(date -u -v-${i}5M +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%SZ)
        fi
        
        aws cloudwatch put-metric-data \
            --namespace AWS/EC2 \
            --metric-name CPUUtilization \
            --dimensions Name=InstanceId,Value=$INSTANCE_ID \
            --value 99.0 \
            --unit Percent \
            --timestamp $TIMESTAMP \
            $ENDPOINT_ARG
    done
done

echo "✅ Stress metrics pushed to CloudWatch!"
echo "CloudSentry's AnomalyDetectorService should flag this during the next cycle."
