# FixSeen Secure Payslip Portal
### Developer Setup, GitHub Workflow & Hetzner/Coolify Deployment Guide

---

## Project Overview

A multi-tenant B2B SaaS platform that eliminates manual HR payroll distribution.

**Core pipeline:**
1. HR uploads a single bulk PDF (e.g. 400-page payslip batch)
2. System scans it asynchronously via Celery — extracts employee IDs using Regex
3. HR reviews a preview screen (orphan pages highlighted in red)
4. On confirmation, Celery splits the PDF, AES-256 encrypts each page with the employee's NRC-derived password, and emails it individually

**Stack:** Django 4.2 · PostgreSQL · Celery + Redis · pikepdf (AES-256) · Coolify · Hetzner VPS

---

## Repository Structure

```
fixseen-portal/
├── config/
│   ├── settings/
│   │   ├── base.py           ← All shared settings
│   │   ├── production.py     ← Hetzner/Docker overrides
│   │   └── development.py    ← Local dev overrides
│   ├── celery.py             ← Celery app + Beat schedule
│   ├── urls.py               ← Project-level URL root
│   └── wsgi.py
│
├── core/
│   ├── models.py             ← Company, User, Employee, CreditBatch, SendJob, ActivityLog
│   ├── secure_utils.py       ← PDF scan + AES-256 encrypt/split engine
│   ├── tasks.py              ← Celery workers (scan, send, expire credits)
│   ├── services.py           ← Credit FIFO deduction, suspend/unsuspend
│   ├── forms.py              ← All Django forms
│   ├── middleware.py         ← Maintenance freeze + suspended account guards
│   ├── access.py             ← Role decorators (@client_required, @super_admin_required)
│   ├── context_processors.py ← Global template context
│   ├── signals.py            ← Login audit logging
│   ├── admin.py              ← Django admin registrations
│   ├── apps.py               ← Registers signals on startup
│   ├── urls_client.py        ← /portal/* routes
│   ├── urls_superadmin.py    ← /admin/* routes
│   ├── views/
│   │   ├── auth_views.py
│   │   ├── client_views.py   ← Full send pipeline: upload→scan→preview→confirm→send
│   │   └── superadmin_views.py
│   └── management/commands/
│       └── expire_credits.py ← Fallback cron for credit expiry
│
├── templates/
│   ├── base.html             ← Full Apple-style design system (CSS vars, components)
│   ├── auth/login.html
│   ├── icons/                ← SVG icon partials
│   ├── superadmin/           ← Super admin dashboard, client list/detail, credits
│   └── client/
│       ├── dashboard.html
│       ├── employees/        ← Staff directory + CSV import
│       ├── send/             ← upload → scanning → preview → processing → complete
│       ├── history.html
│       ├── job_detail.html
│       └── account.html
│
├── Dockerfile                ← Multi-stage, non-root, pikepdf + qpdf included
├── docker-compose.yml        ← Local dev mirror of Coolify production
├── requirements.txt
├── manage.py
├── .env.example              ← Copy to .env, fill in secrets
└── .gitignore
```

---

## Part 1 — Local Development (Nova IDE on Mac)

### Prerequisites
```bash
# Install on Mac if not present
brew install python@3.12 postgresql redis
```

### First-time setup
```bash
# 1. Clone repo
git clone https://github.com/YOUR_ORG/fixseen-portal.git
cd fixseen-portal

# 2. Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Open .env in Nova and fill in your local values:
#   DATABASE_URL=postgres://fixseen:password@localhost:5432/fixseen_dev
#   REDIS_URL=redis://localhost:6379/0
#   SECRET_KEY=any-long-random-string-for-local-use
#   DEBUG=True

# 5. Create local database
createdb fixseen_dev

# 6. Run migrations
python manage.py migrate

# 7. Create super admin
python manage.py createsuperuser
# Then in Django shell: set user.role = "super_admin"
python manage.py shell -c "
from core.models import User
u = User.objects.get(email='your@email.com')
u.role = 'super_admin'
u.is_staff = True
u.save()
print('Done:', u.role)
"

# 8. Start services (three separate Nova terminals)
# Terminal 1 — Django dev server
python manage.py runserver

# Terminal 2 — Celery worker
celery -A config worker --concurrency=2 --loglevel=info

# Terminal 3 — Celery beat (periodic tasks)
celery -A config beat --loglevel=info
```

### Using Docker locally (mirrors production exactly)
```bash
cp .env.example .env   # fill in values
docker-compose up --build
# App at http://localhost:8000
```

---

## Part 2 — GitHub Setup

### Repository & branch strategy
```bash
# Initialize (if starting fresh)
cd fixseen-portal
git init
git add .
git commit -m "feat: initial FixSeen portal scaffold"

# Push to GitHub
git remote add origin https://github.com/YOUR_ORG/fixseen-portal.git
git branch -M main
git push -u origin main
```

### Recommended branch strategy
```
main        ← production (Coolify deploys from here)
staging     ← pre-production testing
dev         ← active development
feature/*   ← individual feature branches
```

### GitHub Secrets (for CI/CD — optional)
In GitHub → Settings → Secrets → Actions, add:
- `SECRET_KEY`
- `DATABASE_URL`
- `REDIS_URL`
- `PDF_HR_MASTER_KEY`
- `EMAIL_HOST_PASSWORD`

---

## Part 3 — Hetzner Server Setup

> ⚠️ Complete this BEFORE setting up Coolify. The server must be a clean Ubuntu 24.04 slate.

### 3.1 — SSH Key (ED25519)
```bash
# On your Mac:
ssh-keygen -t ed25519 -C "fixseen-deploy"
# Add the public key to Hetzner during server provisioning
cat ~/.ssh/id_ed25519.pub   # paste this into Hetzner console
```

### 3.2 — First login & server hardening
```bash
ssh root@YOUR_HETZNER_IP

# Step 1: Swap (prevents OOM on 4GB VPS during heavy PDF processing)
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' | tee -a /etc/fstab

# Step 2: Updates + fail2ban + UFW firewall
apt update && apt upgrade -y
apt install fail2ban -y
systemctl enable fail2ban && systemctl start fail2ban

ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw allow 8000/tcp  # Coolify dashboard
ufw enable

# Step 3: Disable password logins (VERIFY your SSH key works first!)
sed -i -e 's/#PasswordAuthentication yes/PasswordAuthentication no/g' /etc/ssh/sshd_config
sed -i -e 's/PasswordAuthentication yes/PasswordAuthentication no/g' /etc/ssh/sshd_config
systemctl restart ssh

# Step 4: Install Coolify
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
```

Access Coolify at: `http://YOUR_HETZNER_IP:8000`

### 3.3 — DNS
In your domain registrar, set:
```
A    portal.fixseen.com    →    YOUR_HETZNER_IP
A    *.fixseen.com         →    YOUR_HETZNER_IP   (wildcard for Coolify apps)
```

---

## Part 4 — Coolify Deployment

### 4.1 — Create Resources
In Coolify dashboard:
1. **PostgreSQL** → Create resource → note the connection string
2. **Redis** → Create resource → note the connection string

### 4.2 — Connect GitHub
Coolify → Sources → Add GitHub App → authorise your repo

### 4.3 — Create THREE services from the same repo

#### Service 1: Web (Django + Gunicorn)
| Setting | Value |
|---------|-------|
| Source | GitHub → `fixseen-portal` → branch `main` |
| Build | Dockerfile |
| Port | 8000 |
| Domain | `portal.fixseen.com` |
| Start command | `sh -c "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3 --timeout 120"` |

#### Service 2: Celery Worker
| Setting | Value |
|---------|-------|
| Source | Same repo + branch |
| Build | Same Dockerfile |
| Start command | `celery -A config worker --concurrency=2 --loglevel=info` |
| Health check | Disable (workers don't expose HTTP) |

#### Service 3: Celery Beat
| Setting | Value |
|---------|-------|
| Source | Same repo + branch |
| Build | Same Dockerfile |
| Start command | `celery -A config beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler` |

### 4.4 — Environment Variables (all three services)
Paste these into each service's Environment Variables panel:
```env
DJANGO_SETTINGS_MODULE=config.settings.production
SECRET_KEY=your-secret-key
ALLOWED_HOSTS=portal.fixseen.com
DATABASE_URL=postgres://...   (from Coolify PostgreSQL resource)
REDIS_URL=redis://...         (from Coolify Redis resource)
MEDIA_ROOT=/app/media
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=mail.fixseen.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=payslips@fixseen.com
EMAIL_HOST_PASSWORD=your-smtp-password
DEFAULT_FROM_EMAIL=FixSeen Portal <payslips@fixseen.com>
PDF_HR_MASTER_KEY=HR_Admin_Master_Key
```

### 4.5 — Persistent Volume (CRITICAL)
In Coolify → Web service → Volumes → Add:
```
Host path:      /data/fixseen/media
Container path: /app/media
```
Do the same for the Worker service (same paths — both need to read/write payslip files).

### 4.6 — Deploy
```
Deploy Web → Deploy Worker → Deploy Beat
```
Coolify handles SSL (Let's Encrypt) automatically once DNS propagates.

---

## Part 5 — Post-Deployment Checklist

```bash
# SSH into server, then exec into the running web container
docker exec -it $(docker ps -q -f name=fixseen-web) bash

# Create superadmin
python manage.py createsuperuser

# Promote to super_admin role
python manage.py shell -c "
from core.models import User
u = User.objects.get(email='admin@fixseen.com')
u.role = 'super_admin'; u.is_staff = True; u.is_superuser = True
u.save(); print('Role set:', u.role)
"

# Create the first Company
python manage.py shell -c "
from core.models import Company
c = Company.objects.create(name='Acme Corp', slug='acme-corp')
print('Company created:', c)
"
```

---

## Part 6 — Ongoing GitHub Workflow

```bash
# Daily dev flow
git checkout dev
git pull origin dev

# Feature work
git checkout -b feature/employee-bulk-edit
# ... work in Nova ...
git add .
git commit -m "feat: add bulk employee edit from staff directory"
git push origin feature/employee-bulk-edit

# PR → merge to dev → test on staging → merge to main → Coolify auto-deploys
```

### Auto-deploy on push (Coolify Webhooks)
In Coolify → Web service → Webhooks → copy the webhook URL.
In GitHub → repo → Settings → Webhooks → add the Coolify URL.
Now every push to `main` triggers an automatic redeploy.

---

## Key Architecture Decisions

| Decision | Reason |
|----------|--------|
| Celery `--concurrency=2` hard cap | Prevents OOM on 4GB Hetzner VPS during heavy PDF parsing |
| pikepdf for AES-256 | PyPDF2's encryption is RC4 (weak). pikepdf uses qpdf = true AES-256 |
| Credits on Company not User | Allows multiple HR users per company to share credit pool |
| FIFO credit deduction | Oldest-expiry batches consumed first, reducing waste |
| Persistent Docker volume at `/app/media` | Replaces Railway's ephemeral `/tmp/` — survives restarts |
| Maintenance freeze Dec 26–31 | Hardcoded in middleware AND task — can't be bypassed via API |
| Orphan = send blocked | Zero tolerance: no unmatched page can slip through |
| Dual PDF passwords | Employee gets user password, HR has owner password to always recover |

---

## Password Formula Reference

```
Employee: BANDA CHISOMO  (SURNAME FIRSTNAME)
NRC:      123456/78/9

Formula:  NRC_first_4_digits  +  FirstName_initial  +  Surname_initial
Result:   1234                +  C                  +  B
Password: 1234CB
```

If NRC is missing → defaults to `0000XY` where XY are initials.

---

## Credit System Flow

```
Super admin tops up company via portal
            ↓
    CreditBatch created
    (amount, remaining, expires_at = now + 90 days)
            ↓
    HR uploads PDF → scan → preview
            ↓
    HR confirms → matched_count credits RESERVED
            ↓
    Celery processes → deduct_credits() FIFO
    (earliest expiry batch drained first)
            ↓
    Job complete → ActivityLog entry written
            ↓
    Celery Beat (1st of month 00:05)
    → expire_stale_credits() voids overdue batches
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `504 Gateway Timeout` on upload | File is too large or scan is sync — ensure Celery worker is running |
| `OOM / killed` on Celery worker | Worker is processing too many pages concurrently — check `--concurrency=2` |
| PDF pages have no text extracted | PDF is scanned (image-based) — needs OCR (future V2 feature) |
| Orphan pages on every upload | Employee IDs in PDF don't match `employee_id` field — re-check CSV import |
| Emails not sending | Check `EMAIL_HOST_PASSWORD` in env — test with `python manage.py sendtestemail you@email.com` |
| Credits not expiring | Ensure Celery Beat container is running and Beat schedule is seeded via `migrate` |
