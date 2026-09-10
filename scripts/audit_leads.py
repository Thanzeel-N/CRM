"""Read-only duplicate audit. Prints counts, never contact details or credentials."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.database import engine

with engine.connect() as conn:
    print('Database dialect:', engine.dialect.name)
    for label, query in [
        ('Totals by organization', 'SELECT org_id, COUNT(*) AS rows, COUNT(DISTINCT fb_lead_id) AS distinct_source_ids FROM leads GROUP BY org_id'),
        ('Recent dates', 'SELECT org_id, DATE(created_at) AS day, COUNT(*) AS rows, COUNT(DISTINCT fb_lead_id) AS distinct_source_ids FROM leads GROUP BY org_id, DATE(created_at) ORDER BY day DESC LIMIT 15'),
        ('Exact duplicate groups', 'SELECT copies, COUNT(*) AS groups_count FROM (SELECT org_id, fb_lead_id, COUNT(*) AS copies FROM leads GROUP BY org_id, fb_lead_id HAVING COUNT(*) > 1) d GROUP BY copies'),
        ('Same contact and time groups', "SELECT copies, COUNT(*) AS groups_count FROM (SELECT org_id, phone, email, created_at, COUNT(*) AS copies FROM leads WHERE COALESCE(phone, '') <> '' OR COALESCE(email, '') <> '' GROUP BY org_id, phone, email, created_at HAVING COUNT(*) > 1) d GROUP BY copies"),
    ]:
        print(label, [dict(row) for row in conn.execute(text(query)).mappings()])
