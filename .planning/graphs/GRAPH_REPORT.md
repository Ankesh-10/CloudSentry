# Graph Report - CloudSentry  (2026-10-16)

## Corpus Check
- 148 files · ~78,541 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 9 file(s) not represented in the graph (top: (none) 3, .example 2, .toml 2)

## Summary
- 1770 nodes · 4459 edges · 77 communities (60 shown, 17 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 163 edges (avg confidence: 0.9)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `919625c2`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- ActionRunner
- PolicyEngine
- main.py
- request_change
- telemetry_demo.py
- Actions.tsx
- get_supabase_client
- Dashboard.tsx
- scheduler.py
- re
- test_auth.py
- AuthProvider.tsx
- FakeQuery
- actions.py
- anomaly_detector.py
- test_observability.py
- test_endpoints.py
- test_anomaly_detector.py
- anomalies.py
- test_api_surface.py
- test_ml.py
- AWSAdapter
- test_audit.py
- test_telemetry_demo.py
- Findings
- App.tsx
- GCPAdapter
- time
- auth.py
- CloudAdapter
- test_providers.py
- SafetyLayer
- queries.ts
- test_safety_layer.py
- evaluation.py
- resources.py
- AnomalyDetectorService
- package.json
- json
- compilerOptions
- CloudSentry
- get_cloud_adapter
- test_policies_api.py
- policies.py
- devDependencies
- SlidingWindowLimiter
- DiscoveryError
- dependencies
- ui.tsx
- test_resource_protection.py
- Settings
- request_metrics
- Toast.tsx
- _FakeCompute
- scripts
- _FakeConn
- test_aws_adapter.py
- Cloud permissions
- CloudSentry frontend
- CHANGES.md
- Architecture Reality Check
- Codebase Audit & Required Changes
- Required Changes
- .run
- vite.config.ts
- _StalePool
- vite-env.d.ts
- [MEDIUM] API surface does not match the spec (paths, payloads, pagination)
- engines
- @testing-library/jest-dom
- provision_demo.sh
- run_stress.sh
- shutdown_demo.sh

## God Nodes (most connected - your core abstractions)
1. `ActionRunner` - 69 edges
2. `get_supabase_client()` - 54 edges
3. `PolicyEngine` - 42 edges
4. `Findings` - 35 edges
5. `_seed()` - 32 edges
6. `set_flag()` - 31 edges
7. `_h()` - 31 edges
8. `AWSAdapter` - 28 edges
9. `CloudProviderError` - 28 edges
10. `SafetyLayer` - 27 edges

## Surprising Connections (you probably didn't know these)
- `[CRITICAL] JWT verification can be bypassed or is misconfigured` --references--> `get_current_user()`  [INFERRED]
  CHANGES.md → backend/app/auth.py
- `[LOW] Magic numbers, stubs, duplicate imports, unused query params` --references--> `check_idle_compute()`  [INFERRED]
  CHANGES.md → ml/baseline.py
- `[MEDIUM] Documented architecture does not match running architecture` --references--> `GCPAdapter`  [INFERRED]
  CHANGES.md → backend/app/adapters/gcp.py
- `[HIGH] Health check is a stub; Render will not detect backend failure` --references--> `init_db_pool()`  [INFERRED]
  CHANGES.md → backend/app/db/asyncpg_pool.py
- `[MEDIUM] Documented architecture does not match running architecture` --references--> `get_cloud_adapter()`  [INFERRED]
  CHANGES.md → backend/app/adapters/factory.py

## Import Cycles
- None detected.

## Communities (77 total, 17 thin omitted)

### Community 0 - "ActionRunner"
Cohesion: 0.08
Nodes (56): inc(), ActionRunner, _now(), _parse_ts(), set_flag(), [CRITICAL] Detect → decide → act loop is not closed, _action(), aws() (+48 more)

### Community 1 - "PolicyEngine"
Cohesion: 0.05
Nodes (44): CostEstimationService, list_price(), load_pricing(), _size_factor(), _parse_ts(), PolicyEngine, [MEDIUM] APScheduler blocks the API event loop; jobs are independent of documented order, _ec2() (+36 more)

### Community 2 - "main.py"
Cohesion: 0.05
Nodes (32): get_anomaly_summary(), get_cost_trend(), get_dashboard_summary(), _pool(), close_db_pool(), get_pool(), init_db_pool(), is_transaction_pooler() (+24 more)

### Community 3 - "request_change"
Cohesion: 0.05
Nodes (43): detailed_health(), get_system_configs(), list_pending_changes(), automation_enabled(), _clear_pending(), dry_run_mode(), emergency_stop(), get_flag() (+35 more)

### Community 4 - "telemetry_demo.py"
Cohesion: 0.07
Nodes (34): applied_versions(), _database_url(), main(), migration_files(), cleanup(), _client(), inject(), backfill() (+26 more)

### Community 5 - "Actions.tsx"
Cohesion: 0.13
Nodes (35): Badge(), Button(), Card(), ConfirmButton(), Empty(), ErrorBox(), Json(), Loading() (+27 more)

### Community 6 - "get_supabase_client"
Cohesion: 0.10
Nodes (12): fetch_all(), get_supabase_client(), SupabaseNotConfigured, cancel_pending(), is_synthetic(), clear(), live_request(), record_request() (+4 more)

### Community 7 - "Dashboard.tsx"
Cohesion: 0.09
Nodes (43): BarList(), ChartTooltip(), SEG_BG, Segment, Sparkline(), StackedBar(), TrendArea(), CommandPalette() (+35 more)

### Community 8 - "scheduler.py"
Cohesion: 0.07
Nodes (33): is_leader(), release(), try_acquire(), _alert_if_failing(), job_cost(), job_discovery(), job_execute(), job_leadership() (+25 more)

### Community 9 - "re"
Cohesion: 0.06
Nodes (30): configure_logging(), JsonFormatter, RequestIdFilter, scrub(), SecretScrubFilter, TextFormatter, current_client_ip(), current_request_id() (+22 more)

### Community 10 - "test_auth.py"
Cohesion: 0.09
Nodes (36): _claims(), client(), _h(), test_alg_none_rejected(), test_anonymous_session_rejected_even_for_operator_id(), test_any_user_can_emergency_stop(), test_enabling_automation_needs_two_operators(), test_es256_jwks_token_verified() (+28 more)

### Community 11 - "AuthProvider.tsx"
Cohesion: 0.06
Nodes (22): AuthContext, AuthProvider(), AuthState, DEMO_SESSION, Item, NAV, TooltipProvider(), queryClient (+14 more)

### Community 12 - "FakeQuery"
Cohesion: 0.06
Nodes (11): reset_all(), reset_caches(), fake_db(), _reset_process_caches(), _reset_runtime_flags(), _cmp_val(), FakeAPIError, FakeQuery (+3 more)

### Community 13 - "actions.py"
Cohesion: 0.10
Nodes (21): ActionApproval, approve_action(), execute_action(), _get(), get_action(), get_runner(), list_actions(), rollback_action() (+13 more)

### Community 14 - "anomaly_detector.py"
Cohesion: 0.13
Nodes (29): _num(), check_idle_compute(), check_idle_database(), check_low_storage(), check_runaway_lambda(), detect_anomaly_ewma(), detect_anomaly_zscore(), _moved() (+21 more)

### Community 15 - "test_observability.py"
Cohesion: 0.08
Nodes (23): _fmt_labels(), render(), reset(), action_failed(), approvals_needed(), emergency_stop(), job_failing(), reset() (+15 more)

### Community 16 - "test_endpoints.py"
Cohesion: 0.10
Nodes (24): get_metric_summary(), get_metrics(), _pool(), client(), _Conn, _h(), _pending(), _Pool (+16 more)

### Community 17 - "test_anomaly_detector.py"
Cohesion: 0.16
Nodes (32): _cpu_records(), detector(), _idle_records(), _lambda_rows(), _rds(), _rds_records(), _resource(), _seed_active() (+24 more)

### Community 18 - "anomalies.py"
Cohesion: 0.10
Nodes (17): AnomalyUpdate, get_anomaly(), list_anomalies(), update_anomaly_status(), update_anomaly_status_path(), _update_status(), AuditLog, list_audit_logs() (+9 more)

### Community 19 - "test_api_surface.py"
Cohesion: 0.08
Nodes (18): client(), _h(), _healthy_probe(), _StaleConn, test_api_docs_disabled_by_default(), test_config_writes_are_rate_limited(), test_detailed_health_caches_budget(), test_discover_is_rate_limited_per_user() (+10 more)

### Community 20 - "test_ml.py"
Cohesion: 0.15
Nodes (17): [HIGH] ML pipeline is not the documented Isolation Forest system, InferenceEngine, ModelTrainer, _features(), _normal(), test_clean_history_passes_the_promotion_gate(), test_drift_is_measured_against_previous_model(), test_evaluation_does_not_touch_production_models() (+9 more)

### Community 21 - "AWSAdapter"
Cohesion: 0.14
Nodes (4): AWSAdapter, _tags_to_dict(), CloudProviderError, flaky()

### Community 22 - "test_audit.py"
Cohesion: 0.11
Nodes (16): client(), _Conn, _h(), _Pool, _run_retention(), _seed_anomaly(), test_anomaly_change_is_audited_with_transition(), test_anomaly_change_refused_when_audit_unavailable() (+8 more)

### Community 23 - "test_telemetry_demo.py"
Cohesion: 0.10
Nodes (10): backend(), demo(), _history(), run_demo(), _slot(), test_actions_on_demo_resources_are_dry_run(), test_all_normal_raises_nothing(), test_every_scenario_at_once_does_what_list_says() (+2 more)

### Community 24 - "Findings"
Cohesion: 0.08
Nodes (26): [CRITICAL] Discovery upsert will fail: no UNIQUE constraint on `provider_id`, [CRITICAL] Docker / Render image will not build or will not boot as documented, [CRITICAL] JWT verification can be bypassed or is misconfigured, [CRITICAL] Kill-switch is not real; emergency-stop endpoint is missing, [CRITICAL] Lambda “limit” hard-disables the function (concurrency=0), [CRITICAL] Most API endpoints are unauthenticated, including writes, Findings, [HIGH] Apply-tags is mocked as success; AWS tagging is incomplete (+18 more)

### Community 25 - "App.tsx"
Cohesion: 0.17
Nodes (18): Actions, Anomalies, App(), AuditLogs, Dashboard, RequireAuth(), ResetPassword, ResourceDetail (+10 more)

### Community 26 - "GCPAdapter"
Cohesion: 0.12
Nodes (5): GCPAdapter, split_instance_id(), _state(), test_gcp_rejects_unqualified_ids(), test_gcp_requires_project()

### Community 27 - "time"
Cohesion: 0.14
Nodes (12): is_leader(), _budget_status(), collect_health(), models_dir_writable(), _probe_db(), _public_health(), _stale_resources(), client() (+4 more)

### Community 28 - "auth.py"
Cohesion: 0.14
Nodes (13): _decode(), _find_jwk(), get_current_user(), _get_jwks(), is_operator(), is_viewer(), _issuer(), _jwks_url() (+5 more)

### Community 30 - "test_providers.py"
Cohesion: 0.14
Nodes (10): DiscoveryService, aws(), _function(), gcp(), _run_instance(), test_account_is_keyed_by_provider_and_account_id(), test_actions_use_the_resource_region(), test_discovery_covers_every_configured_region() (+2 more)

### Community 31 - "SafetyLayer"
Cohesion: 0.15
Nodes (3): _parse_ts(), SafetyLayer, _truthy()

### Community 32 - "queries.ts"
Cohesion: 0.13
Nodes (8): ActionOp, AuditFilters, useActionOp(), useDiscover(), useInvalidate(), useSetAnomalyStatus(), useSystemHealth(), useUpdateConfig()

### Community 33 - "test_safety_layer.py"
Cohesion: 0.18
Nodes (13): layer(), _res(), test_autoscaled_instances_are_never_stopped(), test_budget_gates_only_cost_increasing_actions(), test_cooldown_counts_only_real_executions(), test_daily_cap(), test_destructive_action_blocked(), test_duplicate_in_flight_blocked() (+5 more)

### Community 34 - "evaluation.py"
Cohesion: 0.16
Nodes (7): evaluate_scenario(), main(), model_features(), generate_from_scenario(), test_generator_is_independent_of_wall_clock(), test_offline_evaluation_on_holdout_meets_targets(), test_strict_evaluation_cli_passes()

### Community 35 - "resources.py"
Cohesion: 0.17
Nodes (10): discovery_status(), get_resource(), list_resources(), _now_iso(), ProtectionUpdate, _resource_bundle(), _run_discovery(), set_protection() (+2 more)

### Community 37 - "package.json"
Cohesion: 0.12
Nodes (16): name, private, type, version, @fontsource-variable/inter, jsdom, @radix-ui/react-dialog, @radix-ui/react-dropdown-menu (+8 more)

### Community 38 - "json"
Cohesion: 0.18
Nodes (5): metadata_path(), read_metadata(), _sklearn_version(), feature_drift(), _feature_stats()

### Community 39 - "compilerOptions"
Cohesion: 0.12
Nodes (15): compilerOptions, isolatedModules, jsx, lib, module, moduleResolution, noEmit, noFallthroughCasesInSwitch (+7 more)

### Community 40 - "CloudSentry"
Cohesion: 0.12
Nodes (15): Access control, API, Cloud providers, CloudSentry, Configuration, Database migrations, Demo environment, Deployment (+7 more)

### Community 41 - "get_cloud_adapter"
Cohesion: 0.17
Nodes (5): get_cloud_adapter(), build_queries(), TelemetryService, [MEDIUM] Documented architecture does not match running architecture, test_build_queries_uses_per_type_windows()

### Community 42 - "test_policies_api.py"
Cohesion: 0.26
Nodes (11): client(), _events(), _h(), _policy(), test_change_not_applied_without_audit(), test_disabling_is_immediate_and_audited(), test_loosening_needs_a_second_operator(), test_only_one_toggle_per_request() (+3 more)

### Community 43 - "policies.py"
Cohesion: 0.25
Nodes (8): _audit(), _get(), get_policy(), list_policies(), _loosens(), Policy, PolicyUpdate, update_policy()

### Community 44 - "devDependencies"
Cohesion: 0.14
Nodes (14): devDependencies, jsdom, tailwindcss, @tailwindcss/vite, @testing-library/jest-dom, @testing-library/react, @testing-library/user-event, @types/node (+6 more)

### Community 45 - "SlidingWindowLimiter"
Cohesion: 0.18
Nodes (5): get_limiter(), _check(), SlidingWindowLimiter, _too_many(), test_sliding_window_limiter_expires_hits()

### Community 46 - "DiscoveryError"
Cohesion: 0.21
Nodes (5): DiscoveryError, [HIGH] Discovery does not collect S3, RDS, or EBS; Lambda mapping is a stub, Trace notes (major paths), test_discover_partial_failure_hides_details(), fail()

### Community 47 - "dependencies"
Cohesion: 0.17
Nodes (12): dependencies, @fontsource-variable/inter, lucide-react, @radix-ui/react-dialog, @radix-ui/react-dropdown-menu, @radix-ui/react-tooltip, react, react-dom (+4 more)

### Community 48 - "ui.tsx"
Cohesion: 0.17
Nodes (9): Delta, NOTICE_ALIAS, NoticeTone, Segmented(), sentence(), STATUS_TONE, statusTone(), Variant (+1 more)

### Community 49 - "test_resource_protection.py"
Cohesion: 0.30
Nodes (9): client(), _h(), _res(), test_not_changed_without_audit(), test_protect_is_immediate_audited_and_blocks_actions(), test_unknown_resource_and_bad_body(), test_unprotect_direct_when_two_person_rule_off(), test_unprotect_needs_a_second_operator() (+1 more)

### Community 50 - "Settings"
Cohesion: 0.25
Nodes (3): Settings, _split(), test_default_threshold_is_stricter_than_the_contamination_boundary()

### Community 51 - "request_metrics"
Cohesion: 0.22
Nodes (6): request_metrics(), _route_template(), security_headers(), unhandled_exception_handler(), _key(), observe()

### Community 52 - "Toast.tsx"
Cohesion: 0.25
Nodes (10): dismiss(), emit(), ICON, items, listeners, push(), toast, Toaster() (+2 more)

### Community 54 - "scripts"
Cohesion: 0.25
Nodes (8): scripts, build, dev, dev:demo, preview, test, test:watch, typecheck

### Community 55 - "_FakeConn"
Cohesion: 0.25
Nodes (3): _FakeConn, _FakePool, __aenter__()

### Community 57 - "Cloud permissions"
Cohesion: 0.29
Nodes (6): Before you attach it, Cloud permissions, Credentials, GCP, Known limits of IAM here, What the policy enforces (independently of the app)

### Community 58 - "CloudSentry frontend"
Cohesion: 0.29
Nodes (6): CloudSentry frontend, Prerequisites, Production, Run locally, Scripts, Supabase setup (once per project)

### Community 59 - "CHANGES.md"
Cohesion: 0.33
Nodes (5): Counts, Documentation vs Implementation Matrix, Files inspected (backend / infra / ML / tests / docs), Production Readiness, Recommended Implementation Order

### Community 60 - "Architecture Reality Check"
Cohesion: 0.33
Nodes (6): Actual Architecture, Architecture Reality Check, Consequences, Differences, Documented Architecture, Recommended Architecture Changes

### Community 61 - "Codebase Audit & Required Changes"
Cohesion: 0.33
Nodes (6): Codebase Audit & Required Changes, Critical Issues, Executive Summary, High Priority, Low Priority, Medium Priority

### Community 62 - "Required Changes"
Cohesion: 0.40
Nodes (5): P0 — Must Fix Before Deployment, P1 — Must Fix Before Production, P2 — Should Fix, P3 — Nice to Have, Required Changes

### Community 64 - "vite.config.ts"
Cohesion: 0.50
Nodes (3): @tailwindcss/vite, vite, @vitejs/plugin-react

## Knowledge Gaps
- **8 isolated node(s):** `@fontsource-variable/inter`, `@radix-ui/react-dropdown-menu`, `@types/node`, `@types/react`, `@types/react-dom` (+3 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 558 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **17 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ActionRunner` connect `ActionRunner` to `PolicyEngine`, `get_supabase_client`, `scheduler.py`, `actions.py`, `test_providers.py`, `SafetyLayer`?**
  _High betweenness centrality (0.040) - this node is a cross-community bridge._
- **Are the 6 inferred relationships involving `ActionRunner` (e.g. with `get_runner()` and `_svc()`) actually correct?**
  _`ActionRunner` has 6 INFERRED edges - model-reasoned connections that need verification._
- **What connects `@fontsource-variable/inter`, `@radix-ui/react-dropdown-menu`, `@types/node` to the rest of the system?**
  _8 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `ActionRunner` be split into smaller, more focused modules?**
  _Cohesion score 0.07547592385218366 - nodes in this community are weakly interconnected._
- **Why does `get_supabase_client()` connect `get_supabase_client` to `ActionRunner`, `PolicyEngine`, `resources.py`, `AnomalyDetectorService`, `request_change`, `scheduler.py`, `get_cloud_adapter`, `policies.py`, `actions.py`, `anomaly_detector.py`, `test_observability.py`, `anomalies.py`, `SafetyLayer`?**
  _High betweenness centrality (0.029) - this node is a cross-community bridge._
- **Are the 10 inferred relationships involving `PolicyEngine` (e.g. with `_svc()` and `AuditLogger`) actually correct?**
  _`PolicyEngine` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Should `PolicyEngine` be split into smaller, more focused modules?**
  _Cohesion score 0.050580997949419004 - nodes in this community are weakly interconnected._