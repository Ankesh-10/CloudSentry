-- Insert default system configs
INSERT INTO system_config (key, value) VALUES
    ('GLOBAL_AUTOMATION_ENABLED', 'false'),
    ('DRY_RUN_MODE', 'true'),
    ('MAX_MONTHLY_BUDGET_USD', '10.00'),
    ('MAX_DAILY_SPEND_USD', '2.00'),
    ('MAX_ACTIONS_PER_DAY', '5'),
    ('ACTION_COOLDOWN_MINUTES', '30'),
    ('MAX_CW_API_CALLS_PER_HOUR', '200')
ON CONFLICT (key) DO NOTHING;

-- Insert default optimization policies
INSERT INTO policies (name, enabled, resource_type, anomaly_type, conditions, action_type, risk_level, requires_approval, priority) VALUES
(
    'Auto-stop idle EC2 instances',
    true,
    'ec2',
    'idle_compute',
    '{"AND": [{"field": "anomaly.idle_score", "op": "gte", "value": 0.80}, {"field": "anomaly.confidence", "op": "gte", "value": 0.75}, {"field": "resource.protected", "op": "eq", "value": false}, {"field": "resource.age_minutes", "op": "gte", "value": 30}]}',
    'stop_ec2',
    'MEDIUM',
    false,
    100
),
(
    'Limit runaway Lambda concurrency',
    true,
    'lambda',
    'runaway_lambda',
    '{"AND": [{"field": "anomaly.confidence", "op": "gte", "value": 0.90}, {"field": "resource.protected", "op": "eq", "value": false}]}',
    'limit_lambda',
    'MEDIUM',
    false,
    100
),
(
    'Require approval for unused EBS volume deletion',
    true,
    'ebs',
    'unused_volume',
    '{"AND": [{"field": "resource.state", "op": "eq", "value": "available"}]}',
    'delete_ebs_volume',
    'HIGH',
    true,
    100
);
