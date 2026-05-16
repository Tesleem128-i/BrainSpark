import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

db_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_UR')

if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor()

migrations = [
    "ALTER TABLE message ADD COLUMN IF NOT EXISTS message_type VARCHAR(20) DEFAULT 'text'",
    "ALTER TABLE message ADD COLUMN IF NOT EXISTS voice_path VARCHAR(255)",
    "ALTER TABLE message ADD COLUMN IF NOT EXISTS pdf_path VARCHAR(255)",
]

for sql in migrations:
    try:
        cur.execute(sql)
        print(f"✅ Done: {sql[:70]}...")
    except Exception as e:
        print(f"⚠️ Skipped: {e}")

cur.close()
conn.close()
print("\n✅ All done!")