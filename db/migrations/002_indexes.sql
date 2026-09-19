-- Covering index for the primary query pattern: fetch metrics for a resource over a time range
CREATE INDEX idx_metrics_resource_time
    ON resource_metrics (resource_id, time DESC);

-- Standard index for metric queries by name and time
CREATE INDEX idx_metrics_name_time
    ON resource_metrics (resource_id, metric_name, time DESC);

-- Indexes for resources
CREATE INDEX idx_resources_provider_id ON resources (provider_id);
CREATE INDEX idx_resources_type ON resources (resource_type);
