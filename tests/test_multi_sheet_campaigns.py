import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_db, Base, engine
from app.models import Organization, User, UserRole, Campaign, Lead, GoogleSheetConnection
from app.services.auth import create_access_token, get_current_user
from sqlalchemy.orm import sessionmaker

TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

def override_get_current_user():
    db = TestingSessionLocal()
    user = db.query(User).filter(User.id == 1).first()
    if not user:
        org = db.query(Organization).filter(Organization.id == 1).first()
        if not org:
            org = Organization(id=1, name="Test Org", slug="test-org")
            db.add(org)
            db.commit()
        user = User(id=1, org_id=1, email="admin@test.com", name="Admin User", role=UserRole.admin, hashed_password="pw")
        db.add(user)
        db.commit()
    db.close()
    return user

client = TestClient(app)

def test_campaign_autodiscovery_and_multi_sheets():
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user

    db = TestingSessionLocal()
    Base.metadata.create_all(bind=engine)
    
    # 1. Create org and lead with campaign_name
    org = db.query(Organization).filter_by(id=1).first()
    if not org:
        org = Organization(id=1, name="Test Org", slug="test-org")
        db.add(org)
        db.commit()

    lead = Lead(
        org_id=1,
        fb_lead_id="test_fb_multi_sheet_123",
        name="John Doe",
        email="john@example.com",
        campaign_name="Summer Promotion 2026",
        form_name="Form 1"
    )
    db.add(lead)
    db.commit()
    db.close()

    # 2. Fetch campaigns list — should auto-discover "Summer Promotion 2026"
    resp = client.get("/campaigns")
    assert resp.status_code == 200
    campaigns = resp.json()
    assert len(campaigns) >= 1
    found_camp = next(c for c in campaigns if c["name"] == "Summer Promotion 2026")
    assert found_camp["lead_count"] >= 1

    # 3. Connect multiple Google Sheets to this campaign
    camp_id = found_camp["id"]
    
    # Sheet 1
    s1_resp = client.post(
        "/integrations/google-sheets/connect",
        json={
            "spreadsheet_url": "https://docs.google.com/spreadsheets/d/11111111111111111111111111111/edit",
            "sheet_name": "Sheet_Primary",
            "campaign_id": camp_id
        }
    )
    assert s1_resp.status_code == 200

    # Sheet 2
    s2_resp = client.post(
        "/integrations/google-sheets/connect",
        json={
            "spreadsheet_url": "https://docs.google.com/spreadsheets/d/22222222222222222222222222222/edit",
            "sheet_name": "Sheet_Backup",
            "campaign_id": camp_id
        }
    )
    assert s2_resp.status_code == 200

    # 4. Verify connections list returns both sheets with campaign name
    conns_resp = client.get("/integrations/google-sheets/connections")
    assert conns_resp.status_code == 200
    conns = conns_resp.json()
    camp_conns = [c for c in conns if c["campaign_id"] == camp_id]
    assert len(camp_conns) == 2
    sheet_names = set(c["sheet_name"] for c in camp_conns)
    assert "Sheet_Primary" in sheet_names
    assert "Sheet_Backup" in sheet_names

    # 5. Verify campaigns endpoint returns connected sheets inside campaign object
    c_list_resp = client.get("/campaigns")
    assert c_list_resp.status_code == 200
    updated_camp = next(c for c in c_list_resp.json() if c["id"] == camp_id)
    assert len(updated_camp["google_sheets"]) == 2
