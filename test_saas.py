"""
Test script: validates multi-tenant isolation, campaign assignment, and agent scoping.
Run: python test_saas.py
"""
import sys, time
import httpx

BASE = "http://127.0.0.1:8000"
PASS = "[PASS]"
FAIL = "[FAIL]"

def check(label, condition, extra=""):
    icon = PASS if condition else FAIL
    print(f"  {icon} {label}" + (f" -- {extra}" if extra else ""))
    return condition

def login(email, password):
    r = httpx.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed for {email}: {r.text}"
    return r.json()

def get(path, token):
    return httpx.get(f"{BASE}{path}", headers={"Authorization": f"Bearer {token}"}).json()

def post(path, token, body):
    return httpx.post(f"{BASE}{path}", json=body, headers={"Authorization": f"Bearer {token}"})

def patch(path, token, body):
    return httpx.patch(f"{BASE}{path}", json=body, headers={"Authorization": f"Bearer {token}"})


print("\n==================================")
print("   MetaCRM Multi-Tenant Test Suite")
print("==================================\n")

# ── 1. Auth & Org Isolation ──
print("1. Authentication & Org Isolation")
acme  = login("admin@acme.com",  "password123")
apex  = login("admin@apex.com",  "password123")
check("Acme admin logged in",  acme["user"]["org_name"] == "Acme Growth Agency")
check("Apex admin logged in",  apex["user"]["org_name"] == "Apex Real Estate Group")
check("Orgs are different",    acme["user"]["org_id"] != apex["user"]["org_id"])

acme_leads = get("/leads", acme["access_token"])
apex_leads = get("/leads", apex["access_token"])
check("Acme sees Acme leads only", all(l["org_id"] == acme["user"]["org_id"] for l in acme_leads), f"{len(acme_leads)} leads")
check("Apex sees Apex leads only", all(l["org_id"] == apex["user"]["org_id"] for l in apex_leads), f"{len(apex_leads)} leads")
check("Tenants are isolated",      not any(l in apex_leads for l in acme_leads))

# ── 2. Campaign Management ──
print("\n2. Campaign Management")
acme_campaigns = get("/campaigns", acme["access_token"])
check("Acme has campaigns", len(acme_campaigns) > 0, f"{len(acme_campaigns)} campaigns")

apex_campaigns = get("/campaigns", apex["access_token"])
check("Apex has campaigns", len(apex_campaigns) > 0, f"{len(apex_campaigns)} campaigns")
check("Campaign orgs isolated", not any(c in apex_campaigns for c in acme_campaigns))

# ── 3. Staff & Agent Scoping ──
print("\n3. Staff & Agent Scoping")
acme_staff = get("/staff", acme["access_token"])
check("Acme has staff", len(acme_staff) >= 2, f"{len(acme_staff)} members")

# Login as agent
agent_data = login("agent.smith@acme.com", "password123")
check("Agent login works", agent_data["user"]["role"] == "agent")

agent_token = agent_data["access_token"]
agent_leads = get("/leads", agent_token)
agent_campaigns = get("/campaigns", agent_token)

check("Agent sees only assigned campaigns", len(agent_campaigns) <= len(acme_campaigns), f"{len(agent_campaigns)} vs {len(acme_campaigns)} total")
check("Agent leads scoped to campaigns",    len(agent_leads) <= len(acme_leads), f"{len(agent_leads)} leads")

# ── 4. Lead Simulate ──
print("\n4. Lead Simulation")
r = post("/leads/simulate", acme["access_token"], {
    "name": "Test Lead", "email": "test@example.com",
    "phone": "+15555550001", "campaign_name": acme_campaigns[0]["name"],
    "form_name": "Test Form"
})
check("Simulate lead created", r.status_code == 200, f"ID #{r.json().get('id')}")

# ── 5. Admin-only endpoints ──
print("\n5. Role Enforcement (Admin-only)")
r = post("/staff/invite", agent_token, {
    "name": "Hacker", "email": "hacker@evil.com",
    "password": "pass123", "role": "agent"
})
check("Agent cannot invite staff (403)", r.status_code == 403)

r = post("/campaigns", agent_token, {"name": "Hacker Campaign"})
check("Agent cannot create campaign (403)", r.status_code == 403)

print("\n==================================")
print("   All tests complete!")
print("==================================\n")
