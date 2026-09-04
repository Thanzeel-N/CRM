# Meta Lead Ads CRM

A multi-tenant CRM for **Meta (Facebook/Instagram) Lead Ads** with WhatsApp follow-up, Google Sheets sync, and Excel export. Built with FastAPI + SQLAlchemy, deployable via Docker or any PaaS.

---

## Features

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
