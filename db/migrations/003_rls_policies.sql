-- Enable RLS
ALTER TABLE cloud_accounts ENABLE ROW LEVEL SECURITY;
ALTER TABLE resources ENABLE ROW LEVEL SECURITY;
ALTER TABLE resource_metrics ENABLE ROW LEVEL SECURITY;
ALTER TABLE cost_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE anomalies ENABLE ROW LEVEL SECURITY;
ALTER TABLE optimization_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_config ENABLE ROW LEVEL SECURITY;

-- Select policies (Allow authenticated users to read all tables)
CREATE POLICY select_cloud_accounts ON cloud_accounts FOR SELECT TO authenticated USING (true);
CREATE POLICY select_resources ON resources FOR SELECT TO authenticated USING (true);
CREATE POLICY select_resource_metrics ON resource_metrics FOR SELECT TO authenticated USING (true);
CREATE POLICY select_cost_records ON cost_records FOR SELECT TO authenticated USING (true);
CREATE POLICY select_anomalies ON anomalies FOR SELECT TO authenticated USING (true);
CREATE POLICY select_optimization_actions ON optimization_actions FOR SELECT TO authenticated USING (true);
CREATE POLICY select_audit_logs ON audit_logs FOR SELECT TO authenticated USING (true);
CREATE POLICY select_policies ON policies FOR SELECT TO authenticated USING (true);
CREATE POLICY select_system_config ON system_config FOR SELECT TO authenticated USING (true);

-- Update policies (Allow authenticated users to update specific tables)
-- Note: Row-Level Security controls row access. Column-level updates (e.g. only allowing 'status' updates)
-- are typically enforced via Supabase's API or PostgreSQL triggers. 
-- Here we allow row updates and assume application-level/API-level constraints on specific fields.
CREATE POLICY update_anomalies ON anomalies FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY update_optimization_actions ON optimization_actions FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY update_policies ON policies FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
CREATE POLICY update_system_config ON system_config FOR UPDATE TO authenticated USING (true) WITH CHECK (true);
