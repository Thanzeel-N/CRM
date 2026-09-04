import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
from app.main import app
from app.models import User, MetaPageConnection, Lead
from app.database import Base, engine, get_db
from app.services.auth import get_current_user
from app.routers.meta_oauth import create_fb_session_token
import httpx
from sqlalchemy.orm import sessionmaker

# Create a test database
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

def override_get_current_user():
    user = User(id=1, org_id=1, email="test@example.com", name="Test User")
    return user

app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_user] = override_get_current_user

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    # Create org and user if not exists
    from app.models import Organization
    if not db.query(Organization).filter_by(id=1).first():
        org = Organization(id=1, name="Test Org", slug="test-org")
        db.add(org)
        db.commit()
    if not db.query(User).filter_by(id=1).first():
        user = User(id=1, org_id=1, email="test@example.com", name="Test User", hashed_password="pw")
        db.add(user)
        db.commit()
    yield
    # Cleanup leads and connections
    db.query(Lead).delete()
    db.query(MetaPageConnection).delete()
    db.commit()
    db.close()


def create_mock_response(json_data):
    mock = MagicMock()
    mock.json.return_value = json_data
    return mock


@pytest.mark.asyncio
async def test_connect_page_missing_permissions():
    """Test that missing required Facebook permissions returns a 403."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        # Mock /me/permissions response missing "leads_retrieval"
        mock_client.get.side_effect = [
            create_mock_response({
                "data": [
                    {"permission": "pages_show_list", "status": "granted"},
                    {"permission": "pages_manage_metadata", "status": "granted"}
                ]
            })
        ]
        
        valid_token = create_fb_session_token("mock_user_token")
        response = client.post("/integrations/facebook/connect", json={
            "fb_session_token": valid_token,
            "page_id": "123",
            "page_name": "Test Page",
            "forms": ["form_1"]
        })
        
        assert response.status_code == 403
        assert "Missing required Facebook permissions" in response.json()["detail"]


@pytest.mark.asyncio
async def test_connect_page_webhook_failure():
    """Test that a webhook subscription failure returns a 400 error."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        # 1. /me/permissions (success)
        # 2. /me/accounts (success)
        mock_client.get.side_effect = [
            create_mock_response({
                "data": [
                    {"permission": "pages_show_list", "status": "granted"},
                    {"permission": "pages_manage_metadata", "status": "granted"},
                    {"permission": "leads_retrieval", "status": "granted"}
                ]
            }),
            create_mock_response({
                "data": [
                    {"id": "123", "access_token": "page_token_123"}
                ]
            })
        ]
        
        # 3. /subscribed_apps (fails)
        mock_client.post.return_value = create_mock_response({
            "error": {"message": "Invalid permissions to subscribe"}
        })
        
        valid_token = create_fb_session_token("mock_user_token")
        response = client.post("/integrations/facebook/connect", json={
            "fb_session_token": valid_token,
            "page_id": "123",
            "page_name": "Test Page",
            "forms": ["form_1"]
        })
        
        assert response.status_code == 400
        assert "Webhook subscription failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_connect_page_successful_sync_with_pagination():
    """Test successful connection, pagination of historical leads, and idempotency."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        # 1. /me/permissions
        # 2. /me/accounts
        # 3. /form_1/leads (page 1)
        # 4. /form_1/leads (page 2)
        mock_client.get.side_effect = [
            create_mock_response({
                "data": [
                    {"permission": "pages_show_list", "status": "granted"},
                    {"permission": "pages_manage_metadata", "status": "granted"},
                    {"permission": "leads_retrieval", "status": "granted"}
                ]
            }),
            create_mock_response({
                "data": [
                    {"id": "123", "access_token": "page_token_123"}
                ]
            }),
            create_mock_response({
                "data": [
                    {"id": "lead_1", "campaign_name": "C1", "form_id": "form_1", "field_data": [{"name": "email", "values": ["a@b.com"]}]}
                ],
                "paging": {"next": "http://graph.facebook.com/next_page"}
            }),
            create_mock_response({
                "data": [
                    {"id": "lead_2", "campaign_name": "C1", "form_id": "form_1", "field_data": [{"name": "email", "values": ["c@d.com"]}]},
                    # Add duplicate lead_1 to test idempotency
                    {"id": "lead_1", "campaign_name": "C1", "form_id": "form_1", "field_data": [{"name": "email", "values": ["a@b.com"]}]}
                ]
            })
        ]
        
        mock_client.post.return_value = create_mock_response({"success": True})
        
        valid_token = create_fb_session_token("mock_user_token")
        response = client.post("/integrations/facebook/connect", json={
            "fb_session_token": valid_token,
            "page_id": "123",
            "page_name": "Test Page",
            "forms": ["form_1"]
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        
        # Check sync stats
        stats = data["sync_results"]
        assert stats["forms_synced"] == 1
        assert stats["leads_imported"] == 2
        assert stats["duplicates_skipped"] == 1
        assert stats["failed_forms"] == 0
        
        # Check database
        db = TestingSessionLocal()
        leads = db.query(Lead).all()
        assert len(leads) == 2
        db.close()


@pytest.mark.asyncio
async def test_connect_page_failed_form_sync():
    """Test that a failed form sync does not crash the entire process, but reports failure."""
    with patch("httpx.AsyncClient") as mock_client_class:
        mock_client = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        
        mock_client.get.side_effect = [
            create_mock_response({
                "data": [
                    {"permission": "pages_show_list", "status": "granted"},
                    {"permission": "pages_manage_metadata", "status": "granted"},
                    {"permission": "leads_retrieval", "status": "granted"}
                ]
            }),
            create_mock_response({
                "data": [
                    {"id": "123", "access_token": "page_token_123"}
                ]
            }),
            # Form 1 fails
            create_mock_response({"error": {"message": "API Error on form 1"}}),
            # Form 2 succeeds
            create_mock_response({
                "data": [
                    {"id": "lead_3", "campaign_name": "C2", "form_id": "form_2", "field_data": [{"name": "email", "values": ["e@f.com"]}]}
                ]
            })
        ]
        
        mock_client.post.return_value = create_mock_response({"success": True})
        
        valid_token = create_fb_session_token("mock_user_token")
        response = client.post("/integrations/facebook/connect", json={
            "fb_session_token": valid_token,
            "page_id": "123",
            "page_name": "Test Page",
            "forms": ["form_1", "form_2"]
        })
        
        assert response.status_code == 200
        stats = response.json()["sync_results"]
        assert stats["failed_forms"] == 1
        assert stats["forms_synced"] == 1
        assert stats["leads_imported"] == 1
