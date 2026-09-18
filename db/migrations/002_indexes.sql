-- Covering index for the primary query pattern: fetch metrics for a resource over a time range
CREATE INDEX idx_metrics_resource_time
    ON resource_metrics (resource_id, time DESC);

-- Partial index for recent data (last 7 days — most-queried window)
CREATE INDEX idx_metrics_recent
    ON resource_metrics (resource_id, metric_name, time DESC)
    WHERE time > NOW() - INTERVAL '7 days';

-- Indexes for resources
CREATE INDEX idx_resources_provider_id ON resources (provider_id);
CREATE INDEX idx_resources_type ON resources (resource_type);
