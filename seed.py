"""
Reseed the database with campaign assignments for multi-tenant SaaS demo.
Run: python seed.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "sqlite:///./lead_crm.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine)
db = Session()

from app.models import Base, Organization, User, UserRole, Lead, LeadStatus, Campaign
from app.services.auth import hash_password
import random, time

Base.metadata.create_all(bind=engine)

def seed():
    # Clear existing
    db.query(Lead).delete()
    db.query(Campaign).delete()
    db.query(User).delete()
    db.query(Organization).delete()
    db.commit()

    # ─── ORG 1: Acme Growth Agency ───────────────────────────────
    org1 = Organization(name="Acme Growth Agency", slug="acme-growth-agency")
    db.add(org1); db.commit(); db.refresh(org1)

    admin1 = User(org_id=org1.id, email="admin@acme.com", name="Sarah Chen",
                  hashed_password=hash_password("password123"), role=UserRole.admin)
    agent1a = User(org_id=org1.id, email="agent.smith@acme.com", name="Agent Smith",
                   hashed_password=hash_password("password123"), role=UserRole.agent)
    agent1b = User(org_id=org1.id, email="jane.doe@acme.com", name="Jane Doe",
                   hashed_password=hash_password("password123"), role=UserRole.agent)
    db.add_all([admin1, agent1a, agent1b]); db.commit()
    db.refresh(admin1); db.refresh(agent1a); db.refresh(agent1b)

    camp1a = Campaign(org_id=org1.id, name="B2B SaaS Outreach Q3",
                      description="LinkedIn lead gen campaign for SaaS decision makers",
                      meta_form_id="form_12345", assigned_user_id=agent1a.id)
    camp1b = Campaign(org_id=org1.id, name="Summer Promo 2026",
                      description="Facebook retargeting for summer discount offers",
                      meta_form_id="form_67890", assigned_user_id=agent1b.id)
    db.add_all([camp1a, camp1b]); db.commit()
    db.refresh(camp1a); db.refresh(camp1b)

    leads_org1 = [
        Lead(org_id=org1.id, campaign_id=camp1a.id, fb_lead_id=f"fb_{int(time.time()*1000)+1}",
             name="Johnathan Vance", email="j.vance@techcorp.io", phone="+14155556789",
             campaign_name=camp1a.name, status=LeadStatus.qualified),
        Lead(org_id=org1.id, campaign_id=camp1a.id, fb_lead_id=f"fb_{int(time.time()*1000)+2}",
             name="Elena Rostova", email="elena@scale.ai", phone="+14155551234",
             campaign_name=camp1a.name, status=LeadStatus.contacted),
        Lead(org_id=org1.id, campaign_id=camp1b.id, fb_lead_id=f"fb_{int(time.time()*1000)+3}",
             name="Marcus Sterling", email="marcus@ventures.io", phone="+12125559876",
             campaign_name=camp1b.name, status=LeadStatus.new),
        Lead(org_id=org1.id, campaign_id=camp1b.id, fb_lead_id=f"fb_{int(time.time()*1000)+4}",
             name="Samantha Reed", email="sam.reed@fusionco.com", phone="+17025554321",
             campaign_name=camp1b.name, status=LeadStatus.converted),
    ]
    db.add_all(leads_org1); db.commit()

    # ─── ORG 2: Apex Real Estate Group ───────────────────────────
    org2 = Organization(name="Apex Real Estate Group", slug="apex-real-estate")
    db.add(org2); db.commit(); db.refresh(org2)

    admin2 = User(org_id=org2.id, email="admin@apex.com", name="James Carter",
                  hashed_password=hash_password("password123"), role=UserRole.admin)
    agent2a = User(org_id=org2.id, email="sarah.agent@apex.com", name="Sarah Williams",
                   hashed_password=hash_password("password123"), role=UserRole.agent)
    db.add_all([admin2, agent2a]); db.commit()
    db.refresh(admin2); db.refresh(agent2a)

    camp2a = Campaign(org_id=org2.id, name="Luxury Villas Dubai 2026",
                      description="High-net-worth property buyers campaign",
                      meta_form_id="form_RE001", assigned_user_id=agent2a.id)
    camp2b = Campaign(org_id=org2.id, name="First-Time Buyer Mortgages",
                      description="Entry-level buyer lead capture",
                      meta_form_id="form_RE002", assigned_user_id=admin2.id)
    db.add_all([camp2a, camp2b]); db.commit()
    db.refresh(camp2a); db.refresh(camp2b)

    leads_org2 = [
        Lead(org_id=org2.id, campaign_id=camp2a.id, fb_lead_id=f"fb_{int(time.time()*1000)+5}",
             name="Brandon Sterling", email="b.sterling@luxe.com", phone="+19715550987",
             campaign_name=camp2a.name, status=LeadStatus.new),
        Lead(org_id=org2.id, campaign_id=camp2a.id, fb_lead_id=f"fb_{int(time.time()*1000)+6}",
             name="Chloe Bennet", email="chloe@property.ae", phone="+14085553456",
             campaign_name=camp2a.name, status=LeadStatus.qualified),
        Lead(org_id=org2.id, campaign_id=camp2b.id, fb_lead_id=f"fb_{int(time.time()*1000)+7}",
             name="Daniel Kim", email="d.kim@firsthome.co", phone="+13105557890",
             campaign_name=camp2b.name, status=LeadStatus.contacted),
    ]
    db.add_all(leads_org2); db.commit()

    print("SEEDED database:")
    print("   Org 1: Acme Growth Agency")
    print("     Admin:  admin@acme.com / password123")
    print("     Agent:  agent.smith@acme.com / password123 -> Campaign: B2B SaaS Outreach Q3")
    print("     Agent:  jane.doe@acme.com / password123 -> Campaign: Summer Promo 2026")
    print("   Org 2: Apex Real Estate Group")
    print("     Admin:  admin@apex.com / password123")
    print("     Agent:  sarah.agent@apex.com / password123 -> Campaign: Luxury Villas Dubai 2026")

if __name__ == "__main__":
    seed()
    db.close()
