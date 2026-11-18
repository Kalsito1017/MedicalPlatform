# MediCore on AWS EC2 + Cloudflare Tunnel — Step-by-Step

> **Demo only.** MediCore uses **synthetic data only**. It is **not** a medical
> device, is **not** clinically validated, and must **not** hold real patient
> data. Cloudflare and AWS will process the demo traffic; do not enter real
> patient information.

This guide deploys the current repository to **one AWS EC2 instance** using
**Docker Compose**, then exposes it through a **Cloudflare Tunnel** with HTTPS.
No load balancer, no NAT gateway, no public database, and no inbound 80/443
ports are required.

```text
Browser → Cloudflare edge (HTTPS) → cloudflared tunnel → app:8000
                                                         ├── PostgreSQL
                                                         └── speech (optional)
```

---

## 0. What you need

| Item | Required? | Notes |
|---|---|---|
| AWS account | Yes | New accounts get **$100 + up to $100** in credits; Free plan lasts 6 months or until credits run out |
| Cloudflare account | Yes | Free plan is enough for Tunnel and DNS |
| Domain name | Recommended | A stable named tunnel needs a domain added to Cloudflare. A domain is the only likely paid item (roughly $10/year). Without one, use a temporary `trycloudflare.com` quick tunnel. |
| GitHub access | Yes | The repository must be cloneable on the instance |
| SSH client | Optional | You can use EC2 Instance Connect or SSM Session Manager instead |

### Free-tier reality

- **AWS:** $100 at sign-up + up to $100 for five activities ($20 each: EC2,
  RDS, Lambda, Bedrock, AWS Budgets). The Free plan ends after **6 months** or
  when credits run out. The account then closes; AWS retains data for 90 days.
- **EC2:** `t3.micro`, `t3.small`, `t4g.micro`, `t4g.small`,
  `c7i-flex.large`, and `m7i-flex.large` are listed as eligible for newer
  accounts. Verify in the console.
- **Cloudflare Tunnel:** free on the Cloudflare free plan.
- **EKS:** do **not** use EKS for this budget. The control plane alone is about
  **$73/month**. Use Docker Compose or optional k3s.
- A public IPv4 address, EBS storage, ECR storage, and data transfer consume
  credits. Set an AWS Budget before launching anything.

---

## 1. Create the AWS account

1. Open <https://aws.amazon.com/free/>.
2. Click **Create an AWS Account**.
3. Enter an email address and password, then choose an account name.
4. Choose the **Free plan** (not the Paid plan) when asked.
5. Verify your email and phone number.
6. Add a payment method if AWS asks for identity verification. The Free plan
   should not charge you unless you upgrade or activate paid-only services.
7. Choose the **Basic support plan** (free).
8. Sign in to the AWS Console.

### 1.1 Secure the root account

1. Open **IAM → Security credentials**.
2. Enable **MFA** for the root user.
3. Do **not** create root access keys.
4. Create an IAM user or, preferably, use IAM Identity Center for daily access.
   For this demo, root is only for account setup.

### 1.2 Create a budget alarm

1. Open **Billing and Cost Management → Budgets**.
2. Create a **Monthly cost budget**.
3. Set a small amount, for example **$10**.
4. Add your email address for alerts.
5. Create the budget.

This is also one of the activities that earns $20 in Free Tier credits.

### 1.3 Choose a region

Use the region selector in the top-right corner. Pick one close to you, for
example `eu-central-1` (Frankfurt) or `us-east-1`. Stay in one region for the
demo.

### 1.4 Optional: earn the extra $100

Open the **Explore AWS** widget on the Console Home and complete the activities:

- launch and terminate an EC2 instance;
- create and delete a small RDS database;
- create a simple Lambda function;
- open a model in the Bedrock playground;
- create an AWS Budget.

Each activity earns **$20**, up to **$100**. Delete any resources the activities
create; they still consume credits while running.

---

## 2. Create the Cloudflare account and add a domain

1. Open <https://dash.cloudflare.com/sign-up>.
2. Create an account and verify your email.
3. Click **Add a site**.
4. Enter your domain (for example `example.com`) and choose the **Free** plan.
5. Cloudflare shows two nameservers. At your domain registrar, replace the
   existing nameservers with Cloudflare's.
6. Wait for Cloudflare to show the zone as **Active**. This can take a few
   minutes to a few hours.
7. Open **Zero Trust** in the Cloudflare dashboard. Choose the **Free** plan and
   create a team name if prompted.

If you do not have a domain, skip the named tunnel and use the **quick tunnel**
in §8.7. The quick tunnel is free but its URL changes when it restarts.

---

## 3. Create the EC2 instance

### 3.1 Create an IAM role for the instance

1. Open **IAM → Roles → Create role**.
2. Trusted entity: **AWS service → EC2**.
3. Attach:
   - `AmazonSSMManagedInstanceCore` — lets you use Session Manager instead of
     public SSH.
   - `AmazonEC2ContainerRegistryReadOnly` — only if you will pull images from
     ECR.
4. Name the role `medicore-ec2-role` and create it.

Do not attach `AdministratorAccess`. Do not create access keys.

### 3.2 Launch the instance

1. Open **EC2 → Instances → Launch instance**.
2. Name: `medicore-demo`.
3. AMI: **Ubuntu Server 24.04 LTS (64-bit x86)**.
   If you choose `t4g.small`, select the **ARM64** Ubuntu 24.04 AMI instead.
4. Instance type:
   - `t3.small` for the simplest x86 setup;
   - `t4g.small` if you prefer ARM and your images support it.
5. Key pair:
   - create `medicore-key.pem` and download it, or
   - choose **Proceed without a key pair** if you will use SSM Session Manager.
6. Network settings:
   - allow **SSH (22)** from **My IP** only, or no SSH at all if using SSM;
   - do **not** open HTTP 80 or HTTPS 443;
   - outbound traffic: allow HTTPS (443).
7. Storage: **30 GiB gp3**.
8. Advanced details:
   - IAM instance profile: `medicore-ec2-role`;
   - leave the rest at defaults.
9. Launch the instance and wait until it is **Running** and has passed status
   checks.

A public IPv4 address is assigned by default. It is fine for outbound Cloudflare
Tunnel traffic, but you do not need to open any inbound web ports.

### 3.3 Connect

**Option A — SSH from your computer:**

```bash
chmod 400 medicore-key.pem
ssh -i medicore-key.pem ubuntu@<EC2_PUBLIC_IP>
```

**Option B — EC2 Instance Connect:**
EC2 Console → select the instance → **Connect** → **EC2 Instance Connect** →
Connect.

**Option C — SSM Session Manager:**
Install the AWS CLI and Session Manager plugin locally, then:

```bash
aws ssm start-session --target <INSTANCE_ID>
```

---

## 4. Install Docker and prepare the server

Run these commands on the EC2 instance.

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-v2 git curl
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Log out and back in (or run `newgrp docker`) so the Docker group applies.

Verify:

```bash
docker --version
docker compose version
```

If `docker-compose-v2` is unavailable:

```bash
sudo apt install -y docker-compose-plugin
```

or use Docker's convenience installer:

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"
```

### 4.1 Add swap (recommended on 2 GB)

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

This helps if you later run the optional whisper.cpp speech container.

---

## 5. Get the project

```bash
cd ~
git clone https://github.com/Kalsito1017/MedicalPlatform.git
cd MedicalPlatform
```

If the repository is private, authenticate with GitHub CLI, a deploy key, or a
personal access token. Never put a token in the repository or in a command that
will be saved in shell history.

---

## 6. Create the `.env` file

The app reads secrets and configuration from the repository-root `.env`. It is
gitignored. Generate fresh values; do not reuse local demo passwords.

```bash
cd ~/MedicalPlatform

APP_SECRET_KEY=$(openssl rand -base64 48)
POSTGRES_PASSWORD=$(openssl rand -hex 24) # security-check-allow: generated at deploy time, not a literal secret
ADMIN_PASSWORD=$(openssl rand -hex 16)
DOCTOR_PASSWORD=$(openssl rand -hex 16)
NURSE_PASSWORD=$(openssl rand -hex 16)

cat > .env <<EOF
APP_ENV=development
APP_PORT=127.0.0.1:8000
PUBLIC_ORIGIN=http://localhost:8000
API_BASE_URL=http://localhost:8000
ALLOWED_ORIGINS=http://localhost:8000

APP_SECRET_KEY=${APP_SECRET_KEY}
POSTGRES_USER=app
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POSTGRES_DB=clinical_demo

SESSION_COOKIE_NAME=cap_session
CSRF_COOKIE_NAME=csrf_token
SESSION_TTL_HOURS=8
CLINIC_TIMEZONE=Europe/Sofia

RUN_MIGRATIONS_ON_START=1
LOG_LEVEL=INFO
REQUEST_MAX_BODY_BYTES=1048576

LLM_API_BASE_URL=https://opencode.ai/inference/go
LLM_MODEL=deepseek-v4.1-flash
LLM_CORRECTION_MODEL=gpt-6-luna
LLM_API_KEY=

WHISPER_BASE_URL=http://speech:8080
VOICE_MAX_UPLOAD_BYTES=4194304
VOICE_MAX_DURATION_SECONDS=60
VOICE_FFMPEG_TIMEOUT_SECONDS=30

DEMO_ADMIN_EMAIL=admin@example.com
DEMO_ADMIN_PASSWORD=${ADMIN_PASSWORD}
DEMO_DOCTOR_EMAIL=doctor@example.com
DEMO_DOCTOR_PASSWORD=${DOCTOR_PASSWORD}
DEMO_NURSE_EMAIL=nurse@example.com
DEMO_NURSE_PASSWORD=${NURSE_PASSWORD}

CLOUDFLARE_TUNNEL_TOKEN=
EOF

chmod 600 .env
```

Notes:

- `APP_PORT=127.0.0.1:8000` binds the app to localhost only. Cloudflare Tunnel
  reaches it through the Compose network; the public internet cannot reach port
  8000.
- `APP_ENV=development` is for the initial local HTTP check. Switch to
  `production` after the HTTPS tunnel is working.
- `LLM_API_KEY` is optional. Leave it empty to run without AI; AI endpoints will
  return `503` and the rest of the app works.
- `WHISPER_BASE_URL` can stay at the Compose default. If you do not start the
  speech profile, voice transcription returns `503`; manual scheduling works.
- The demo passwords are in `.env`. Open it with `nano .env` or
  `grep DEMO_ .env` when you need to sign in. Do not share or commit them.

---

## 7. Start the database and app

```bash
cd ~/MedicalPlatform

docker compose up -d --build db app
docker compose ps
docker compose logs --tail=100 app
```

Wait until the app is healthy. The entrypoint runs Alembic migrations before
starting Uvicorn.

### 7.1 Seed the synthetic data

```bash
docker compose run --rm app python /app/scripts/seed_demo_data.py
```

The seed is idempotent. It creates the synthetic patients, encounters, labs,
prescriptions, appointments, and the three demo staff accounts using the
`DEMO_*` values from `.env`.

### 7.2 Verify locally

```bash
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
```

Both should return `200`.

To open the app locally from your computer, create an SSH tunnel:

```bash
ssh -i medicore-key.pem -L 8000:127.0.0.1:8000 ubuntu@<EC2_PUBLIC_IP>
```

Then browse `http://localhost:8000` on your computer. Sign in with one of the
demo accounts and confirm the patient list appears.

---

## 8. Create the Cloudflare Tunnel

### 8.1 Create the tunnel

1. Open **Cloudflare Zero Trust → Networks → Tunnels**.
2. Click **Create a tunnel**.
3. Choose **Cloudflared**.
4. Name it `medicore-demo` and save.
5. Choose **Docker** as the connector.
6. Copy the tunnel token. Do not paste it into a public chat or commit it.

### 8.2 Add the public hostname

1. Open the tunnel → **Public Hostname** → **Add a public hostname**.
2. Subdomain: `demo` (or another name).
3. Domain: your Cloudflare domain.
4. Path: leave empty.
5. Service:
   - Type: **HTTP**
   - URL: `app:8000` (if the dashboard expects a full URL, enter `http://app:8000`)
6. Save. Cloudflare creates the proxied DNS record automatically.

### 8.3 Add the token to `.env`

```bash
cd ~/MedicalPlatform
nano .env
```

Set:

```dotenv
CLOUDFLARE_TUNNEL_TOKEN=<the token from Cloudflare>
```

Save and keep the file at mode `600`:

```bash
chmod 600 .env
```

### 8.4 Switch the app to production HTTPS

In `.env`, change:

```dotenv
APP_ENV=production
PUBLIC_ORIGIN=https://demo.example.com
API_BASE_URL=https://demo.example.com
ALLOWED_ORIGINS=https://demo.example.com
```

Use your real hostname. Do not include a trailing slash or a path.

### 8.5 Start the tunnel overlay

```bash
cd ~/MedicalPlatform
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d
```

This starts:

- `db`
- `app` (recreated with the new `.env`)
- `cloudflared` (reads `TUNNEL_TOKEN` from the overlay)

Check:

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml ps
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml logs --tail=100 cloudflared
```

A healthy connector logs a registered tunnel connection.

### 8.6 Verify the public URL

```bash
curl -fsS https://demo.example.com/health/live
curl -fsS https://demo.example.com/health/ready
```

Then open `https://demo.example.com` in a browser and sign in with a demo
account.

### 8.7 If you do not have a domain: quick tunnel

Use this only for a temporary demo. The URL changes when the process restarts.

Install `cloudflared` on the EC2 host:

```bash
curl -L --output cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
cloudflared --version
```

For ARM (`t4g`), use `cloudflared-linux-arm64.deb`.

Start a quick tunnel:

```bash
cloudflared tunnel --url http://localhost:8000
```

It prints a `https://<random>.trycloudflare.com` URL. Copy that URL into `.env`:

```dotenv
APP_ENV=development
PUBLIC_ORIGIN=https://<random>.trycloudflare.com
API_BASE_URL=https://<random>.trycloudflare.com
ALLOWED_ORIGINS=https://<random>.trycloudflare.com
```

Restart the app:

```bash
docker compose up -d app
```

Because the URL changes on restart, this is not suitable for a stable demo. Use
a named tunnel with a domain for anything you need to share.

---

## 9. Test the application

Sign in with each demo role and check:

- [ ] Dashboard loads, role/nav are correct.
- [ ] Patient list and search work.
- [ ] Patient detail tabs: Summary, Labs, Prescriptions, Encounters,
      Vitals & outcomes.
- [ ] ICD-10 diagnosis block shows codes/descriptions; a Doctor can assign a
      code; a Nurse cannot.
- [ ] Appointments: Month, Week, Day, Year, Upcoming.
- [ ] Create/reschedule/cancel an appointment.
- [ ] Messages: inbox/sent, compose, optional attachment.
- [ ] Admin: staff list, create account, role grant/revoke, audit table.
- [ ] Analytics: date range, KPIs, sections.
- [ ] System Logs: live stream and filters.
- [ ] Print / Save as PDF: A4 report, diagnoses/ICD-10 table, repeating header,
      page numbers.
- [ ] Language switcher EN/BG; print labels follow the selected language.
- [ ] Optional AI/voice: only if configured.

For the PDF, turn off the browser's **Headers and footers** option for the
cleanest output; the app prints its own header/footer in the page margins.

---

## 10. Optional: enable speech

Speech is optional and CPU/RAM-heavy. On a 2 GB instance, add the 2 GB swap from
§4.1 first.

```bash
cd ~/MedicalPlatform
docker compose --profile speech up -d --build speech
docker compose ps
```

The app's Compose configuration already points `WHISPER_BASE_URL` at
`http://speech:8080`. Restart the app:

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d app
```

Test voice scheduling. If the instance runs out of memory:

```bash
docker compose stop speech
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d app
```

Manual scheduling and AI text proposals continue to work without speech.

---

## 11. Optional: Kubernetes bonus with k3s

Stop the Compose stack first; both stacks compete for ports and the database:

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml down
```

Install k3s:

```bash
curl -sfL https://get.k3s.io | sh -
sudo k3s kubectl get nodes
```

Build and import the app image:

```bash
cd ~/MedicalPlatform
docker build -f backend/Dockerfile -t medicalplatform-app:local .
docker save medicalplatform-app:local | sudo k3s ctr images import -
```

Create the namespace and secret, then apply the manifests:

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/app-deployment.yaml
kubectl apply -f k8s/app-service.yaml
```

`k8s/secret.yaml` is gitignored and may not exist yet. Create it from
`k8s/secret.example.yaml`, or run `scripts/k8s/gen-secret.sh` as described in
`docs/KUBERNETES.md`.

Before applying, edit:

- `k8s/configmap.yaml`: production origins, `APP_ENV=production`,
  `RUN_MIGRATIONS_ON_START=0`.
- `k8s/app-service.yaml`: use `ClusterIP`.
- `k8s/app-deployment.yaml`: the imported image, probes, resources.
- `k8s/postgres.yaml`: local-path PVC for the demo.

Run migrations once as a Job, then the seed Job if needed. Do not run the seed in
production.

For Cloudflare Tunnel in k3s, create a `cloudflared` Deployment with a
`TUNNEL_TOKEN` Secret and point the public hostname at the app Service. With the
provided `k8s/app-service.yaml`, the service port is `8081`, so use:

```text
http://app:8081
```

The full k3s/Cloudflare runbook, including the Deployment YAML, is in
`docs/AWS_FREE_TIER_DEMO_PLAN.md` §6.

k3s is a bonus, not required. Docker Compose is the simpler demo.

---

## 12. Updates and backups

### Update the app

```bash
cd ~/MedicalPlatform
git pull
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml build app
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d app
```

Purge the Cloudflare cache if you changed static assets.

### Back up the database

```bash
cd ~/MedicalPlatform
docker compose exec -T db pg_dump -U app clinical_demo > ~/medicore-backup.sql
ls -lh ~/medicore-backup.sql
```

Copy the dump off the instance (for example with `scp`) and store it encrypted.
Do not commit it. Test a restore before relying on it.

### Restore

```bash
cd ~/MedicalPlatform
cat ~/medicore-backup.sql | docker compose exec -T db psql -U app clinical_demo
```

### EBS snapshot

Before a risky change, create an EBS snapshot of the instance volume in the EC2
console. Delete old snapshots to avoid credit usage.

---

## 13. Teardown

### Stop the app

```bash
cd ~/MedicalPlatform
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml down
```

`down` keeps the database volume. `down -v` deletes it permanently:

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml down -v
```

### Delete Cloudflare resources

1. Zero Trust → Networks → Tunnels → delete `medicore-demo`.
2. Delete the public hostname/DNS record if it remains.
3. Remove the domain from Cloudflare if you no longer need it.

### Delete AWS resources

1. EC2 → Instances → terminate `medicore-demo`.
2. Delete the EBS volume and any snapshots.
3. Release any Elastic IP.
4. Delete ECR repositories/images if you used ECR.
5. Delete S3 backup objects if you created a bucket.
6. Delete SSM parameters/secrets if you used them.
7. Check **Billing → Budgets** and the credit balance.

The Free plan account closes after 6 months or when credits run out. Export
anything you need before then.

---

## 14. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Cloudflare shows 502 | Tunnel points at the wrong service or app is unhealthy | Set the public hostname service to `http://app:8000`; check `docker compose ps` and app logs |
| Tunnel connector restarts | Bad/revoked token | Regenerate the token in Cloudflare and update `.env` |
| Login loops or cookies not saved | `PUBLIC_ORIGIN`/`ALLOWED_ORIGINS` mismatch, or `APP_ENV`/HTTPS mismatch | Use the exact HTTPS hostname, no trailing slash; set `APP_ENV=production` behind the tunnel; restart app |
| 403 CSRF | Cookies from a different hostname or a stale session | Use one hostname consistently; sign out/in; clear cookies |
| Database unhealthy | Wrong password, DB not ready, or volume problem | `docker compose logs db`; check `POSTGRES_*`; wait for health |
| App container restarts | Missing `APP_SECRET_KEY`, DB unavailable, or migration failure | `docker compose logs app`; fix `.env`; wait for DB |
| Out of memory | whisper.cpp plus app/Postgres on 2 GB | Add swap; stop speech; use `m7i-flex.large` or disable voice |
| `docker: permission denied` | User not in the docker group | Log out/in or `newgrp docker` |
| Port 8000 already in use | Another process or stack | Change `APP_PORT` or stop the other process |
| Cloudflare serves stale JS/CSS | Edge cache | Purge cache; bypass `/api/*` and HTML |
| System Logs stream stalls | Proxy buffering SSE | Add a cache/bypass rule for `/api/v1/admin/logs/stream` |
| Seed fails | DB not healthy or migrations failed | Wait for `db` health; check `docker compose logs app`; rerun seed |
| `git clone` asks for credentials | Private repository | Authenticate with GitHub CLI/deploy key; never commit the token |

Useful commands:

```bash
docker compose ps
docker compose logs --tail=200 app
docker compose logs --tail=200 db
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml logs --tail=200 cloudflared
curl -i http://127.0.0.1:8000/health/ready
```

---

## 15. Security and cost rules

- **Synthetic data only.** Do not enter real patient data.
- Do not open inbound 80/443, 5432, or 8080. Cloudflare Tunnel is outbound-only.
- Use SSM Session Manager instead of public SSH where possible.
- Keep the Cloudflare token and `.env` secret; rotate the token if exposed.
- Do not commit `.env`, database dumps, keys, or tokens.
- Use a least-privilege EC2 instance role; no `AdministratorAccess`.
- Set an AWS Budget and check the credit balance.
- A domain is the only likely paid item; a quick tunnel is free but temporary.
- EKS is not a free-tier path; use Compose or k3s for this demo.
- Cloudflare and AWS see the demo traffic; the app is not HIPAA/GDPR certified
  and is not a medical device.

---

## 16. Command cheat sheet

```bash
# Connect
ssh -i medicore-key.pem ubuntu@<EC2_PUBLIC_IP>

# Project
cd ~/MedicalPlatform

# Start database + app
docker compose up -d --build db app

# Seed synthetic data
docker compose run --rm app python /app/scripts/seed_demo_data.py

# Health
curl -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready

# Start with Cloudflare Tunnel
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d

# Logs
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml logs --tail=100 cloudflared

# Update
git pull
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml build app
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d app

# Backup
docker compose exec -T db pg_dump -U app clinical_demo > ~/medicore-backup.sql

# Stop
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml down
```

For the deeper AWS/k3s/IAM rationale, see
`docs/AWS_FREE_TIER_DEMO_PLAN.md` and `docs/AWS_EKS_IAM_PLAN.md`.
