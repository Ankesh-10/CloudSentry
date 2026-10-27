# CloudSentry frontend

React + TypeScript + Vite dashboard for the CloudSentry API: overview, resources and telemetry,
anomalies, action approval/execution/rollback, the audit log, system health and runtime configuration.

* **Auth:** Supabase email/password sign-in (PKCE). The access token is sent as `Authorization: Bearer …`
  to the API; roles (viewer/operator) are enforced by the API. A 401 triggers one token refresh and retry;
  if that fails the session is dropped and the sign-in page says it expired. A 403 on a state-changing call
  switches the UI to read-only. "Forgot password?" sends a Supabase recovery email that lands on `/reset-password`.
* **Data:** only through `/api/v1/*` (never direct table reads), so rate limits, role checks and the audit
  trail always apply. Lists poll every 30 s.
* **Stack:** React 19, React Router, TanStack Query, Recharts, Tailwind CSS 4, Vitest.

## Prerequisites

- Node.js **20.19+** (22 LTS or 24 recommended) and npm
- The backend running on `http://localhost:8000` (see the root README)
- A Supabase user with a CloudSentry role: add its id to `VIEWER_USER_IDS` / `OPERATOR_USER_IDS` in the
  backend `.env`, or set `app_metadata.cloudsentry_role` with the service-role key

## Supabase setup (once per project)

In the Supabase dashboard for the project:

1. **Authentication → URL Configuration:** set *Site URL* to the frontend origin, and add
   `http://localhost:5173/reset-password` plus `https://<your-frontend>/reset-password` to *Redirect URLs*.
   Without this, password-reset emails link to the wrong place.
2. **Authentication → Sign In / Providers:** turn off *Allow new users to sign up* unless every new user is
   meant to see data (`REQUIRE_VIEWER_ROLE=false`). Create users from the dashboard (*Users → Add user*) instead.
3. **Grant a role:** add the user's id to `VIEWER_USER_IDS` / `OPERATOR_USER_IDS` in the backend `.env`, or set
   `app_metadata.cloudsentry_role` (`viewer` | `operator`) with the service-role key.
4. **Keys:** the frontend needs only the URL and the publishable (or legacy anon) key. The service-role key and
   database URL stay in the backend `.env`.

Free-tier projects pause after a week without activity; the API then fails its readiness check and sign-in
fails with a network error. Restore the project from the dashboard.

## Run locally

```bash
cd frontend
npm install
cp .env.example .env.local   # set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY
npm run dev                  # http://localhost:5173
```

In dev, Vite proxies `/api` to `VITE_DEV_API_PROXY` (default `http://localhost:8000`), so no CORS setup
is needed. With Docker: `docker compose up --build` from the repo root starts both services.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Dev server with hot reload |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest unit/component tests |
| `npm run build` | Type-check and build to `dist/` |
| `npm run preview` | Serve the production build locally |

## Production

Build with `VITE_API_BASE_URL` set to the backend origin (e.g. `https://cloudsentry-backend.onrender.com`)
and add the frontend's origin to the backend's `CORS_ORIGINS`. `dist/` is static: any static host works
(`render.yaml` defines a Render static site). Rewrite all paths to `/index.html` for client-side routing.

`VITE_*` values are compiled into the bundle and are public. Only the Supabase **publishable/anon** key belongs here,
never the service-role key.
