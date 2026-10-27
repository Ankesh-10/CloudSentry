# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

A solo developer who runs their own cloud account (AWS first, a GCP project optionally) and pays the bill themselves. They aren't a full-time cost or platform engineer. They check in now and then to see what's idle, what's spiking and what CloudSentry did or wants to do, and they approve or roll back actions. Most of the time, CloudSentry watches the account for them.

The product also has the roles from the backend: **viewer** (reads everything, can trigger an emergency stop) and **operator** (approves, executes and rolls back actions, changes config, runs discovery). For a solo user both roles are usually the same person. The two-person rule (`REQUIRE_TWO_PERSON_CONFIG`) exists, but a lone operator has no second person to confirm with.

## Product Purpose

Cost monitoring by hand fails without anyone noticing: idle instances, runaway functions and spikes go unseen until the bill arrives. CloudSentry runs the whole loop itself. It discovers resources, collects telemetry, detects anomalies (rules, z-score/EWMA, Isolation Forest), proposes optimizations through policies, and executes safe, reversible actions. Each action is then verified and audited.

Success means the user leaves CloudSentry running unattended and trusts it, because it can be shown that nothing it does is destructive, hidden or irreversible.

## Positioning

**Safe autonomy.** Cost Explorer and CloudWatch dashboards show data and stop there. CloudSentry acts, and its trust comes from safety you can see:

- a kill-switch (`GLOBAL_AUTOMATION_ENABLED` off by default);
- dry-run by default (`DRY_RUN_MODE`, which logs "WOULD act" instead of acting);
- protection tags;
- daily caps, cooldowns and budgets;
- two-person confirmation for any change that loosens a limit;
- live re-checks before each action, then verification and rollback afterwards;
- an append-only audit log. Approvals, rejections, proposals, policy toggles, protection changes and cancellations are written to it before the change happens; if that write fails, the change is refused.

The product's main promise is that every action the agent might take can be seen, explained, reversed and stopped. Detection quality matters, but it serves that promise.

## Operating Context

- Six areas: Overview, Resources (with resource detail), Anomalies, Actions, Audit log, System (config, policies, job health, pending two-person changes).
- An action's lifecycle: proposed → approved (when the policy requires it) → executing → verified/completed, or failed, expired or rolled back. Proposals expire after `APPROVAL_TTL_HOURS`.
- Emergency stop is open to every role, is always applied, and cancels any pending enable.
- Background jobs run on a schedule: discovery every 15 min, telemetry every 5 min, nightly retraining, cost and retention jobs. Job status is persisted and shown on the System page.
- Alerts go to a Slack-compatible webhook: approvals waiting, failed actions, verification timeouts, rollbacks, emergency stops, failing jobs.
- Sign-in uses Supabase Auth. Plain sign-up grants nothing. Roles come from allowlists or `app_metadata.cloudsentry_role`.
- A demo mode (`npm run dev:demo`, `frontend/src/lib/demo.ts`) runs the dashboard without a live backend.

## Capabilities and Constraints

- **Actions:** `stop_ec2`, `limit_lambda` and `apply_tags` (all reversible), plus `recommend_review` (no cloud call). Destructive actions are blocked in code and denied in IAM. RDS is never mutated, only recommended for review.
- **Never stopped automatically:** instances owned by Auto Scaling groups, EKS node groups, Karpenter or EC2/Spot fleets.
- **Explainability:** each anomaly records the resource, metric, observed value, expected range, score, confidence, detecting model/version, and a reason in plain English.
- **AWS:** EC2, Lambda, S3, RDS and EBS across `AWS_REGIONS`; CloudWatch telemetry; list prices from `data/pricing/`. A type missing from a regional file falls back to the us-east-1 price, logged as an approximation, and the UI must not present that figure as exact.
- **GCP:** Compute VMs only. Monitoring support has not been tested against a live project. No labels and no pricing yet.
- **Out of scope:** automatic deletion, Kubernetes monitoring, AWS Organizations billing, Reserved Instances / Savings Plans, sub-minute streaming.
- **Terminology:** the UI uses the backend's words: resource, anomaly, action, policy, proposal, protected/exempt, kill-switch/automation, dry-run, emergency stop, audit log, two-person confirmation.
- **Stack (existing):** React 19, Vite, Tailwind CSS 4, TanStack Query, Recharts, Radix primitives, Phosphor icons; FastAPI + Supabase Postgres backend.

## Evidence on Hand

- Demo data in `frontend/src/lib/demo.ts`; a Terraform demo environment in `terraform/`.
- Offline ML evaluation: `python -m ml.evaluation --strict` (deterministic, runs in CI). The README cites offline precision rising from about 0.7 to 0.9+ at the default IF threshold.
- PRD at `md/CloudSentry_PRD.md`.
- **None of these exist:** customers, testimonials, logos, savings figures from real accounts, or press. Future work must not invent them. Any savings shown must come from real or labelled demo data.

## Product Principles

1. **Safety is shown, never hidden.** Automation state, dry-run, caps, protection and pending confirmations are always visible. A user should never wonder whether the agent is armed.
2. **Explain before act.** Every proposal and anomaly shows why it was raised before it asks for a decision.
3. **Reversible by default, honest when not.** Rollback is close at hand. Actions that can't be undone or that only recommend are labelled as such.
4. **The audit trail is the record.** Anything the agent or a person changes can be traced from the UI to its audit entry.
5. **Built for one person checking in.** The overview answers "anything I need to deal with?" at a glance, and the serious controls (emergency stop, approve) stay within reach.

## Accessibility & Inclusion

- WCAG 2.2 AA is the required bar across the dashboard.
- Light and dark themes are both first-class (`frontend/src/theme/ThemeProvider.tsx`).
- It must work on a phone: emergency stop, approving or rejecting an action, and checking status all have to be practical at phone width.
