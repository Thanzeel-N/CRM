# Meta Lead Ads CRM

A multi-tenant CRM for **Meta (Facebook/Instagram) Lead Ads** with WhatsApp follow-up, Google Sheets sync, and Excel export. Built with FastAPI + SQLAlchemy, deployable via Docker or any PaaS.

---

## Features

- **Today workspace** — due and overdue follow-ups, local-time scheduling, completion and call shortcuts. In-app reminders refresh every minute while the tab is visible; no email or push notifications.
- **Lead ownership and activity** — admins assign individual owners; otherwise campaign assignment applies. Calls, notes, status changes and scheduling actions record the actor and time.
- **Safe campaign archiving** — archive and restore campaigns without deleting leads or Sheets connections. Archiving is organizational; it does not unsubscribe Meta forms or stop lead capture.
- **Reliable delivery** — Meta webhook events and Google Sheets deliveries persist in a database queue, retry up to five times with backoff, and expose failures and manual retry in Lead Sources.
- **Operational analytics** — overdue counts, first recorded response time, and conversion comparisons by campaign and current owner. Existing leads have no response measurement until a new call or status change is recorded.

- **Multi-tenant** — one database, isolated data per organization
- **Meta Lead Ads webhook** — real-time lead ingestion with HMAC signature validation
- **WhatsApp Cloud API** — outbound messages + inbound reply logging
- **Google Sheets sync** — auto-append new leads (requires service account)
- **Excel export** — one-click `.xlsx` download
- **JWT auth** — bcrypt-hashed passwords, 30-day tokens
- **Role-based access** — Admin (full access) / Agent (assigned campaigns only)

---

## Tech Stack

| Layer | Tech |
|---|---|
| API | FastAPI 0.115 |
| ORM | SQLAlchemy 2.0 + Alembic |
| DB (prod) | MySQL 8 via PyMySQL |
| DB (dev) | SQLite |
| Auth | PyJWT + passlib[bcrypt] |
| Server | Gunicorn + Uvicorn workers |
| Container | Docker + Docker Compose |

---

## Project Structure

```
lead-crm/
├── app/
│   ├── main.py           # FastAPI app, logging, CORS, exception handler
│   ├── config.py         # Pydantic settings (reads .env)
│   ├── database.py       # SQLAlchemy engine & session
│   ├── models.py         # Organization, User, Lead, Campaign, ...
│   ├── schemas.py        # Pydantic request/response models
│   ├── routers/
│   │   ├── auth.py       # Register, login, /me
│   │   ├── leads.py      # CRUD, status updates, Excel export, WhatsApp send
│   │   ├── campaigns.py  # Campaign management
│   │   ├── staff.py      # Staff/agent management
│   │   ├── orgs.py       # Organization settings
│   │   ├── webhooks.py   # Meta Lead Ads + WhatsApp webhook receivers
│   │   ├── meta_oauth.py # Facebook OAuth page connection flow
│   │   └── google_sheets.py  # Google Sheets connection management
│   ├── services/
│   │   ├── auth.py       # JWT helpers, bcrypt hashing
│   │   ├── meta.py       # Graph API calls
│   │   └── whatsapp.py   # WhatsApp Cloud API send helper
│   └── static/           # Bundled SPA (index.html, app.js, styles.css)
├── Dockerfile
├── docker-compose.yml
├── Procfile              # For Heroku / Railway / Render
├── requirements.txt
├── alembic.ini
├── migrations/
├── .env.example
└── seed.py               # Dev seed data
```

---

## Quick Start (Local Dev)

```bash
# 1. Clone
git clone https://github.com/Thanzeel-N/CRM.git
cd CRM

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — for local dev, the SQLite default works out of the box

# 5. Run
uvicorn app.main:app --reload
```

Visit **http://localhost:8000** → SPA dashboard  
Visit **http://localhost:8000/docs** → Swagger UI (dev only, disabled in production)

---

## Production Deployment

### Repair repeated Meta leads

Every import path now enforces one row per `(org_id, fb_lead_id)` in the database, including concurrent webhook and manual sync requests. Phone numbers and email addresses are not submission identities: different Meta lead IDs remain separate enquiries.

Run `python scripts/audit_leads.py` on the server to compare stored rows with distinct Meta/source IDs. It only reads counts and does not print contact information.

For the duplicate repair, stop the application/worker processes, take a database backup, deploy this version and run `alembic upgrade head`, then start the application. Revision `b422_unique_leads` consolidates rows with the same source ID within each organization and creates the unique index. It retains the oldest record ID, combines notes, moves related history, preserves scheduling/ownership information, and archives every original row in `lead_duplicate_archive` for recovery. Duplicate queued Sheets deliveries are consolidated; existing rows already appended to external Sheets are not removed.

Repeat the audit after migration. For 11 distinct Meta IDs stored three times, the CRM row count changes from 33 to 11. If the audit reports 33 distinct IDs, this repair deliberately does not guess which submissions should be merged.

Before starting the updated application, run `alembic upgrade head` once against the deployment database. Revision `b421_workflow` adds workflow fields, activity history and the integration queue without removing existing leads. Development startup applies the additive schema changes automatically. Production startup requires migrations to have completed.

The application runs the retry worker through its ASGI lifespan. Keep lifespan enabled and at least one application process running. Pending jobs survive restarts; abandoned processing jobs become eligible after their ten-minute lease. The worker polls every ten seconds and retries after increasing delays; admins can retry failed jobs in **Lead Sources → Sync activity**.

Google Sheets rows retain the six existing fields and include a **CRM Marker** column. Keep this column and its values intact; its position is determined by the header, not a fixed column letter. Deliveries update the row with the matching marker, including status, owner, notes and follow-up changes. New form questions add columns without moving existing answers. Values are written as literal text so phone numbers and answers are preserved. A database lease serializes deliveries to each spreadsheet/tab across workers; retries after uncertain responses reuse the same row. Manual concurrent changes to worksheet structure or deliveries that outlive the ten-minute lease still require care.

### Forms and regional timezones

Revision `b424_sheets_timezone` adds form filters, worksheet delivery locks and an organization timezone. Install the updated requirements (including `tzdata`), back up the database, run `alembic upgrade head`, and restart the application. For a local SQLite database inside this checkout, `python scripts/upgrade_local_database.py` creates a backup before migrating.

In **Lead Sources → Region and timezone**, India (`Asia/Kolkata`, IST) is the default. Select another IANA region or use **Use my device’s region**, then save. The saved organization setting applies to every staff member, lead date filters, Today counts, follow-ups, charts, Excel and Google Sheets. Database timestamps remain UTC. Daylight-saving regions use their regional rules; skipped or ambiguous follow-up times require another time or an explicit offset through the API. Changing the timezone queues updates for connected sheet rows.

To split one campaign's forms, create separate empty tabs, then choose **Connect Google Sheet → Target Campaign → Lead form → Worksheet / Tab Name**. Connect each form to its tab. Choose **All forms** for a combined campaign tab; the Form/Form ID columns distinguish submissions and custom question columns expand as needed. The same destination cannot be configured with a conflicting campaign/form rule. Connections and reconnects queue historical leads through the same retry worker rather than clearing the worksheet. Check **Sync activity** for delivery results and errors; a saved connection does not mean Google has accepted its rows yet.

Old exports with blank/missing CRM markers cannot be matched reliably to source submissions. Sync preserves those tabs and reports an actionable error instead of clearing them or appending duplicates. Keep the old tab for review and connect a new empty tab to produce a clean export from the CRM. Existing duplicates in external Sheets are not silently deleted. Different Meta lead IDs remain separate enquiries even when phone/email match.

Meta lead fetch failures now remain failures rather than generating sample contact data. Unknown or ambiguously connected Facebook pages are ignored. The webhook tester is restricted to admins.

### Option A — Docker Compose (recommended)

```bash
cp .env.example .env
# Fill in all required values in .env

docker compose up -d --build
```

The app will be available on port **8000**. Put Nginx/Caddy in front for HTTPS.

### Option B — Heroku / Railway / Render

Deploy directly from this repo. Set all environment variables from `.env.example` in your platform's dashboard.

```bash
# Heroku example
heroku create my-crm-app
heroku config:set APP_ENV=production SECRET_KEY=... DATABASE_URL=...
git push heroku main
```

### Run Alembic Migrations (MySQL production)

```bash
# Apply all migrations
alembic upgrade head

# After schema changes, generate a new migration
alembic revision --autogenerate -m "describe change"
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `APP_ENV` | Yes | `development` or `production` |
| `DATABASE_URL` | Yes | MySQL: `mysql+pymysql://user:pass@host/db` |
| `SECRET_KEY` | Yes | Long random string — **never use the default in production** |
| `HOST_URL` | Yes | Your public domain, e.g. `https://crm.example.com` |
| `ALLOWED_ORIGINS` | No | Comma-separated extra CORS origins |
| `META_APP_ID` | Yes | From Meta Developer Dashboard |
| `META_APP_SECRET` | Yes | From Meta Developer Dashboard |
| `META_WEBHOOK_VERIFY_TOKEN` | Yes | Must match Meta Dashboard webhook config |
| `WHATSAPP_PHONE_NUMBER_ID` | No | For WhatsApp Cloud API |
| `WHATSAPP_ACCESS_TOKEN` | No | For WhatsApp Cloud API |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | No | For WhatsApp webhook verification |
| `GOOGLE_SHEETS_CREDENTIALS_FILE` | No | Path to GCP service account JSON |

---

## API Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/register` | — | Register org + admin user |
| `POST` | `/auth/login` | — | Login, returns JWT |
| `GET` | `/auth/me` | JWT | Current user info |
| `GET` | `/leads` | JWT | List leads (filterable) |
| `POST` | `/leads` | JWT | Create lead manually |
| `GET` | `/leads/stats` | JWT | Dashboard statistics |
| `GET` | `/leads/export/excel` | JWT | Export as `.xlsx` |
| `GET` | `/leads/{id}` | JWT | Get single lead |
| `PATCH` | `/leads/{id}/status` | JWT | Update status + notes |
| `DELETE` | `/leads/{id}` | JWT | Delete lead |
| `POST` | `/leads/whatsapp/send` | JWT | Send WhatsApp message |
| `GET` | `/campaigns` | JWT | List campaigns |
| `POST` | `/campaigns` | JWT (admin) | Create campaign |
| `PATCH` | `/campaigns/{id}` | JWT (admin) | Update campaign |
| `DELETE` | `/campaigns/{id}` | JWT (admin) | Delete campaign |
| `GET/POST` | `/webhooks/meta` | HMAC | Meta Lead Ads webhook |
| `GET/POST` | `/webhooks/whatsapp` | Token | WhatsApp webhook |
| `GET` | `/integrations/facebook/auth-url` | JWT | Start FB OAuth |
| `POST` | `/integrations/facebook/connect` | JWT | Connect FB page |
| `POST` | `/integrations/google-sheets/connect` | JWT | Connect Google Sheet |
| `GET` | `/api/health` | — | Liveness probe |

---

## Meta Webhook Setup

1. Go to **Meta Developer Dashboard** → your app → Webhooks
2. Subscribe to the `leadgen` field
3. Set callback URL: `https://YOUR_DOMAIN/webhooks/meta`
4. Set verify token: same value as `META_WEBHOOK_VERIFY_TOKEN` in `.env`

For local testing, expose your server with:
```bash
ngrok http 8000
```

---

## Google Sheets Integration

1. Create a **Service Account** in [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the **Google Sheets API** for your project
3. Download the JSON key file
4. Share your Google Sheet with the service account email
5. Set `GOOGLE_SHEETS_CREDENTIALS_FILE=/path/to/key.json` in `.env`

---

## Security Notes

- Passwords are hashed with **bcrypt** (cost factor 12)
- All protected endpoints require a **Bearer JWT** — there is no unauthenticated fallback
- Meta webhook `POST` payloads are validated with **HMAC-SHA256** in production
- Swagger UI (`/docs`) is **disabled** when `APP_ENV=production`
- Never commit `.env` or credential files (both are in `.gitignore`)
