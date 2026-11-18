# Kubernetes (local, production-shaped)

Run the Clinical AI Platform on a **local Kubernetes cluster** (Docker Desktop
Kubernetes) as the production-shaped environment: Postgres as a StatefulSet
with persistent storage, the single-origin app image behind probes and
resource limits, secrets injected from a Kubernetes Secret, and a one-shot
seed Job. This is the orchestration counterpart of `docker-compose.yml`; the
runtime contract (image, entrypoint, env vars, health endpoints) is identical.

> Synthetic-data demo — not for clinical use.

- Namespace: `clinical` (all resources; short on purpose — it appears in every
  command and in the in-cluster DB DNS name `db.clinical.svc`).
- Local URL: **http://localhost:8081** (LoadBalancer on the host; port choice
  explained under "Design choices").
- Commands below assume the `docker-desktop` kubectl context. The helper
  scripts use `--context docker-desktop` explicitly (your current context is
  never changed); override with `KUBE_CONTEXT=<name> scripts/k8s/up.sh`.

## 1. Prerequisites

The **one manual step**: enable Kubernetes in Docker Desktop.

1. Open Docker Desktop.
2. Settings → Kubernetes → check "Enable Kubernetes".
3. Click "Apply & Wait" (Docker Desktop restarts its one-node cluster).
4. Verify:

   ```sh
   kubectl config get-contexts docker-desktop
   ```

   The context must be listed. The helper scripts print exactly this message
   if it is missing:

   ```
   error: kubectl context 'docker-desktop' not found.
   Enable Kubernetes in Docker Desktop -> Settings -> Kubernetes -> Apply,
   then re-run this script (or set KUBE_CONTEXT to your cluster's context).
   ```

Everything else (image build, secret generation) is scripted below.

## 2. Build the app image

The manifests use `medicalplatform-app:local` with `imagePullPolicy:
IfNotPresent` — Docker Desktop Kubernetes sees locally built images directly;
no registry is involved. From the repository root, either:

```sh
docker compose build app                                    # image tagged medicalplatform-app:latest
docker tag medicalplatform-app:latest medicalplatform-app:local
```

or build the tag directly (same Dockerfile, same build context):

```sh
docker build -f backend/Dockerfile -t medicalplatform-app:local .
```

Rebuild + retag whenever backend/frontend code changes, then
`kubectl --context docker-desktop rollout restart deployment/app -n clinical`.

## 3. Secrets

Secrets are **never committed**. `k8s/secret.yaml` is gitignored
(`k8s/.gitignore`) and generated locally:

```sh
scripts/k8s/gen-secret.sh        # writes k8s/secret.yaml (mode 600) with random values
```

- `gen-secret.sh` generates `APP_SECRET_KEY` (`openssl rand -base64 48`), a
  random database password (used for `POSTGRES_PASSWORD` and both
  `DATABASE_URL`s), and random demo-account passwords (printed once to the
  terminal). `LLM_API_KEY` is included only if you `export LLM_API_KEY=...`
  first.
- Alternative: `cp k8s/secret.example.yaml k8s/secret.yaml` and replace every
  `REPLACE_ME` value by hand. The example file holds clearly-fake placeholders
  only.
- Never run `kubectl apply -f k8s/` wholesale: the directory contains both
  `secret.example.yaml` (template) and your `secret.yaml` (real, local) —
  `scripts/k8s/up.sh` applies an explicit file list to avoid the clash.

## 4. Run it

```sh
scripts/k8s/up.sh       # apply everything, wait for rollout, print the URL
scripts/k8s/status.sh   # pods, services, PVCs, probe config/status, events
scripts/k8s/seed.sh     # seed demo data (AFTER the app is Ready)
scripts/k8s/down.sh     # delete namespace -- DATA LOSS (asks for confirmation)
```

Step by step:

1. `scripts/k8s/up.sh` — applies namespace, ConfigMap, Secret, Postgres
   StatefulSet + PVC + Service, app Deployment + Service; waits for both
   rollouts; prints the local URL.
2. Wait until the app pod reports `1/1 Running` (migrations run on app start —
   the same `docker-entrypoint.sh` trade-off as compose; see
   `docs/DEPLOYMENT.md`).
3. `scripts/k8s/seed.sh` — applies the one-shot seed Job
   (`python /app/scripts/seed_demo_data.py` in the app image) and follows its
   log. **Manually applied, never part of `up.sh`.** The seed is idempotent;
   re-running never duplicates rows. If `DEMO_*_PASSWORD` values are missing
   from the secret, random passwords are printed once in the Job log.
4. Open **http://localhost:8081** and log in with a seeded demo account.

### Ports and the local URL

The LoadBalancer Service publishes host port **8081** → container port 8000:

- 8000 is held by the docker-compose stack on the same machine,
- 8080 is commonly used by other local dev tools — Docker Desktop's
  LoadBalancer (an envoy proxy container) fails to bind a busy host port and
  the Service stays `EXTERNAL-IP <pending>`.

If 8081 is busy on your machine, change `port` in `k8s/app-service.yaml` and
`PUBLIC_ORIGIN` / `API_BASE_URL` / `ALLOWED_ORIGINS` in `k8s/configmap.yaml`
to match. Fallback that always works (no LoadBalancer needed):

```sh
kubectl --context docker-desktop port-forward -n clinical svc/app 8081:8081
```

An optional Ingress manifest for later ingress-nginx use is provided as
`k8s/ingress.example.yaml` (not applied by `up.sh`).

## 5. Manifest inventory

| File | Creates |
| --- | --- |
| `k8s/namespace.yaml` | Namespace `clinical` |
| `k8s/configmap.yaml` | ConfigMap `app-config` — non-secret app config (env contract) |
| `k8s/secret.example.yaml` | Template for the Secret (placeholders only; never applied as-is by `up.sh`) |
| `k8s/secret.yaml` | Secret `app-secret` — generated locally, gitignored, never committed |
| `k8s/postgres.yaml` | StatefulSet `db` (postgres:16-alpine) + Service `db` (ClusterIP) + PVC `data-db-0` (1Gi via volumeClaimTemplates) |
| `k8s/app-deployment.yaml` | Deployment `app` (1 replica, `medicalplatform-app:local`) with probes, resources, hardened security context, and a `wait-for-db` init container |
| `k8s/app-service.yaml` | Service `app` (LoadBalancer, host 8081 → container 8000) |
| `k8s/seed-job.yaml` | Job `seed-demo-data` (one-shot `python /app/scripts/seed_demo_data.py`) |
| `k8s/ingress.example.yaml` | Optional Ingress for ingress-nginx (not applied automatically) |

Helpers: `scripts/k8s/up.sh`, `down.sh`, `seed.sh`, `status.sh`,
`gen-secret.sh` (POSIX bash, `set -euo pipefail`, `shellcheck`-clean).

## 6. Design choices

- **LoadBalancer, not Ingress, for the local URL.** Docker Desktop maps
  LoadBalancer Services to a localhost port with zero extra components;
  ingress-nginx would be another install to maintain. The Ingress manifest is
  provided for the future (see "Moving to real production").
- **Secret handling.** All secret values live in one Secret (`app-secret`)
  loaded with `envFrom`, mirroring compose's `env_file` semantics: the image
  contains no secrets, values arrive as environment variables, and startup
  validation reports missing/invalid variables **by name only**
  (`backend/app/config.py`). `stringData` in the manifests keeps the template
  human-readable; Kubernetes base64-encodes on write. `POSTGRES_*` are in the
  same Secret so the DB bootstrap and the app DSN cannot drift apart.
- **ConfigMap + Secret split** (`envFrom` both): non-secret knobs
  (origins, session/CSRF names, LLM toggles) are editable with
  `kubectl apply -f k8s/configmap.yaml` + rollout restart; secrets are only
  touched by `gen-secret.sh`.
- **Probes** mirror `scripts/healthcheck.py` semantics:
  `livenessProbe` → `GET /health/live` (process liveness, never depends on
  DB/LLM), `readinessProbe` → `GET /health/ready` (checks the database), plus
  a `startupProbe` on `/health/live` that absorbs the migrations-on-start step
  (up to 60s). Compose parity: same endpoints, comparable timings
  (10s period, 5s timeout).
- **`wait-for-db` init container.** Compose gates the app with
  `depends_on: condition: service_healthy`; in Kubernetes the app would
  otherwise crash-loop while Postgres initializes (observed during live
  verification: 3 restarts from `ConnectionRefusedError` during `alembic
  upgrade head`). The init container (same image) waits for the TCP port and
  removes the race.
- **Resources.** App and DB each run with modest requests/limits
  (cpu 100m/500m, memory 256Mi/512Mi) so the one-node cluster is never
  starved; the init container gets 50m/64Mi.
- **Security context.** App (and seed Job) run as uid 10001
  (`runAsNonRoot`), `readOnlyRootFilesystem: true` with an `emptyDir` on
  `/tmp`, `capabilities.drop: [ALL]`, `allowPrivilegeEscalation: false`,
  `seccompProfile: RuntimeDefault`. **Verified live**: the app starts, runs
  migrations, and serves probes with a read-only root filesystem (the image
  sets `PYTHONDONTWRITEBYTECODE=1`; the only write path is `/tmp`).
  The Postgres container deliberately keeps the image's default privilege
  model (only `allowPrivilegeEscalation: false` + RuntimeDefault seccomp): the
  official image's entrypoint starts as root to fix ownership of a fresh data
  volume and then drops to the `postgres` user via `su-exec`; forcing
  `runAsNonRoot` or dropping capabilities breaks first-time initialization.
- **StatefulSet + volumeClaimTemplates** for Postgres: the PVC (`data-db-0`)
  outlives pod restarts and is the unit of "full reset" (below). Default
  StorageClass (`standard`, rancher.io/local-path on Docker Desktop) is used
  for portability. Like compose, port 5432 is not published to the host.
- **Seed as a Job, not `kubectl exec`.** Same image, `restartPolicy: Never`,
  `backoffLimit: 0`, and `command` replaces the image ENTRYPOINT so migrations
  do not run twice. Documented as manually applied after the app is Ready.

## 7. Environment variables

Configuration arrives as environment variables (ConfigMap `app-config` +
Secret `app-secret` via `envFrom`); the canonical list is `backend/app/config.py`
and the deployment template in `Instructions.md`. Names and purpose only — values live in
`k8s/secret.yaml` locally and are never documented here.

| Variable | Source | Purpose |
| --- | --- | --- |
| `APP_ENV` | ConfigMap | development/test/production mode (kept `development` on the local cluster — see note below) |
| `APP_SECRET_KEY` | Secret | **Required.** Session/signing key (32+ random bytes) |
| `PUBLIC_ORIGIN` | ConfigMap | Origin the browser loads the app from (`http://localhost:8081`) |
| `API_BASE_URL` | ConfigMap | Backend base URL used by the frontend |
| `DATABASE_URL` | Secret | **Required.** Async SQLAlchemy DSN → `db.clinical.svc` |
| `MIGRATION_DATABASE_URL` | Secret | Optional separate DSN for migrations (falls back to `DATABASE_URL`) |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Secret | DB bootstrap (first start of `db-0`) and DSN credentials |
| `SESSION_COOKIE_NAME` / `SESSION_TTL_HOURS` | ConfigMap | Session cookie name and lifetime |
| `CSRF_COOKIE_NAME` | ConfigMap | CSRF cookie name |
| `LLM_ENABLED` | ConfigMap | explicit kill switch; remove it or set `true` to auto-enable AI when `LLM_API_KEY` is present |
| `LLM_API_BASE_URL` / `LLM_MODEL` / `LLM_MAX_TOOL_CALLS` / `LLM_MAX_TOOL_CALLS_GENERAL` / `LLM_REQUEST_TIMEOUT_SECONDS` | ConfigMap | LLM endpoint/model/budget tuning (`LLM_MAX_TOOL_CALLS_GENERAL=10` by default) |
| `LLM_API_KEY` | Secret (optional) | Only when AI is enabled; omitted by default |
| `DEMO_ADMIN_*` / `DEMO_DOCTOR_*` / `DEMO_NURSE_*` | Secret | Seed-time demo logins (synthetic, never real credentials) |
| `ALLOWED_ORIGINS` | ConfigMap | CORS allow-list (must include the local URL) |
| `REQUEST_MAX_BODY_BYTES` | ConfigMap | Request body limit |
| `RUN_MIGRATIONS_ON_START` | ConfigMap | `1` = entrypoint applies Alembic migrations on start |
| `LOG_LEVEL` | ConfigMap | Log verbosity |

**Enabling the LLM (optional).** The app is fully functional without it. To
turn AI features on: put `LLM_API_KEY` into `k8s/secret.yaml` (uncomment the
line in the template, or re-run `LLM_API_KEY=... scripts/k8s/gen-secret.sh`
with `FORCE=1`), remove/set `LLM_ENABLED: "true"` in `k8s/configmap.yaml`, re-apply
both, and `kubectl --context docker-desktop rollout restart deployment/app
-n clinical`.

**Note on `APP_ENV`.** The infrastructure is production-shaped, but the app is
served over plain HTTP on localhost. `APP_ENV=development` keeps session
cookies usable without TLS and `/docs` open for the demo. Switch to
`production` only behind real TLS (it enables the strict cookie/docs
behavior documented in `docs/DEPLOYMENT.md` §6).

## 8. Data persistence and full reset

- The database lives in PVC `data-db-0` (1Gi, StorageClass `standard`) and
  **survives pod restarts, reschedules, and `down.sh`-less re-applies**.
- Full reset (the documented demo-reset path — equivalent of
  `docker compose down -v`):

  ```sh
  kubectl --context docker-desktop delete pod db-0 -n clinical            # optional
  kubectl --context docker-desktop delete pvc data-db-0 -n clinical       # DATA LOSS
  scripts/k8s/up.sh && scripts/k8s/seed.sh
  ```

- Nuclear option: `scripts/k8s/down.sh` deletes the whole namespace
  (everything above plus config). It asks for confirmation because all data
  is lost.

## 9. Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ImagePullBackOff` on `medicalplatform-app:local` | Image not built/tagged on this machine | Build + retag (section 2); `docker images \| grep medicalplatform` must show `:local`. `IfNotPresent` uses the local image; it does not pull from a registry |
| `CrashLoopBackOff` on the app pod | Missing/invalid config — the app validates at startup and names (never values) the bad variables | `kubectl --context docker-desktop logs -n clinical deploy/app \| tail` — look for `Missing variables: ...` / `Invalid variables: ...`; fix `k8s/secret.yaml` (required: `APP_SECRET_KEY`, `DATABASE_URL`) or `k8s/configmap.yaml`, re-apply, rollout restart |
| App pod restarting early in its life | DB not up yet when migrations ran | Normal for the first seconds (init container + `RUN_MIGRATIONS_ON_START`); repeated restarts → check `kubectl logs -n clinical db-0` |
| Probes failing (`Unhealthy` events) | App not answering `/health/live` or `/health/ready` | `kubectl --context docker-desktop exec -n clinical deploy/app -- python /app/scripts/healthcheck.py --url http://localhost:8000 --endpoint /health/ready`; `/health/ready` failing usually means the DB connection is broken (check `db-0` and the DSN in the secret) |
| Service `app` stuck `EXTERNAL-IP <pending>` | Host port already taken (the LoadBalancer binds a localhost port) | Change the Service `port` + the origin vars in `k8s/configmap.yaml` (section 4), or use `kubectl port-forward -n clinical svc/app 8081:8081` |
| PVC `Pending` | No default StorageClass / provisioner not running | Docker Desktop ships `standard` (rancher.io/local-path): `kubectl --context docker-desktop get sc` and `kubectl --context docker-desktop get pods -n local-path-storage`; on other clusters set `storageClassName` in `k8s/postgres.yaml` |
| Seed Job fails | Run before the app finished migrations | Wait for the app pod `1/1 Running`, then re-run `scripts/k8s/seed.sh` |
| `kubectl` commands fail with context errors | Kubernetes not enabled in Docker Desktop | Section 1 (the one manual step) |
| Port-forward or curl refused | Wrong port / app not Ready | `scripts/k8s/status.sh`; URL is `http://localhost:8081` |

## 10. Moving to real production

The local cluster is production-shaped, not production. For a real deployment
follow `docs/DEPLOYMENT.md` (Neon Postgres, Render backend, Cloudflare Pages
frontend) and `docs/BACKLOG.md` (hardening backlog). Mapping:

| Local (this doc) | Real production |
| --- | --- |
| Local image `medicalplatform-app:local` (`IfNotPresent`) | Push to a registry (GHCR/ECR) and deploy by immutable tag or digest; CI builds on merge (`.github/`) |
| Postgres StatefulSet + PVC (`data-db-0`) | Managed Postgres (Neon — `docs/DEPLOYMENT.md` §2): TLS DSN, least-privilege role, `MIGRATION_DATABASE_URL` for elevated migrations |
| `k8s/secret.yaml` (gitignored local file) | Secret manager (Kubernetes External Secrets / cloud secret store / sealed-secrets); never a file in git |
| LoadBalancer on localhost:8081 | Ingress (`k8s/ingress.example.yaml`) + TLS (cert-manager or a managed LB); set `APP_ENV=production`, real `PUBLIC_ORIGIN`/`API_BASE_URL`/`ALLOWED_ORIGINS` |
| 1 replica, fixed resources | Replicas ≥ 2 (app is stateless — sessions in cookies/DB), HPA on CPU/RSS, PodDisruptionBudget; migrations moved to a separate one-shot Job with `RUN_MIGRATIONS_ON_START=0` (see `scripts/docker-entrypoint.sh`) |
| PVC as backup story | Automated Postgres backups/PITR (managed provider), restore drill; audit-log retention policy (`docs/SECURITY.md`) |
| Demo seed Job | Never run the seed in production; bootstrap per `docs/DEPLOYMENT.md` §9 |

**Plan B — AWS EKS + IAM.** If the project must move to AWS, read
**`docs/AWS_EKS_IAM_PLAN.md`** before creating any resource. For a
$100–$200 Free Tier demo, use **one EC2 + Docker Compose** and optionally
**k3s on the same instance** for the Kubernetes bonus; see
**`docs/AWS_FREE_TIER_DEMO_PLAN.md`**. The local manifests are not directly
production-safe: replace the in-cluster Postgres with RDS, replace
`secret.yaml` with External Secrets + Secrets Manager, use an ALB Ingress with
ACM instead of a public LoadBalancer, and add EKS Pod Identity/IRSA service
accounts. IAM is the main risk: one role per workload, no wildcard policies, no
long-lived keys, and GitHub OIDC instead of static CI credentials.

Also carry over the non-k8s concerns already documented: CORS/HTTPS/cookie
rules (`docs/DEPLOYMENT.md` §6), rollback procedure (§8), the verification
checklist (§7), and the claims discipline in `docs/CONCEPT.md` (no clinical
suitability claims).
