import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.config import settings
from app.routers import leads, webhooks, auth, orgs, campaigns, staff, meta_oauth, google_sheets

# ── Logging setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO if settings.app_env == "production" else logging.DEBUG,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Production config validation ───────────────────────────────────────────────
settings.validate_production()

# ── App factory ────────────────────────────────────────────────────────────────
is_production = settings.app_env == "production"

app = FastAPI(
    title="Meta Lead Ads CRM",
    description="Multi-tenant CRM for Meta Lead Ads with WhatsApp follow-up and Google Sheets sync.",
    version="1.0.0",
    # Disable interactive docs in production to reduce attack surface
    docs_url=None if is_production else "/docs",
    redoc_url=None if is_production else "/redoc",
    openapi_url=None if is_production else "/openapi.json",
)

# ── CORS ───────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Database ───────────────────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)

# Auto-migration helper for SQLite dev environments
try:
    with engine.connect() as conn:
        from sqlalchemy import text
        conn.execute(text("ALTER TABLE google_sheet_connections ADD COLUMN campaign_id INTEGER REFERENCES campaigns(id)"))
        conn.commit()
except Exception:
    pass

# ── Global exception handler ───────────────────────────────────────────────────
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception on %s %s\n%s",
        request.method,
        request.url,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred."},
    )

# ── Routers ────────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(leads.router)
app.include_router(campaigns.router)
app.include_router(staff.router)
app.include_router(webhooks.router)
app.include_router(meta_oauth.router)
app.include_router(google_sheets.router)

# ── Static files ───────────────────────────────────────────────────────────────
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
def read_root():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"status": "Meta Lead Ads CRM API Running"}


@app.get("/privacy-policy", include_in_schema=False)
def read_privacy_policy():
    policy_file = os.path.join(static_dir, "privacy-policy.html")
    if os.path.exists(policy_file):
        return FileResponse(policy_file)
    return {"error": "Privacy policy not found"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi import Response
    return Response(status_code=204)


@app.get("/api/health", tags=["health"])
def health_check():
    """Liveness probe — returns 200 when the server is running."""
    return {"status": "ok", "env": settings.app_env}
