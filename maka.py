import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

db_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_UR')

if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

if '?' in db_url:
    db_url = db_url.split('?')[0]

conn = psycopg2.connect(db_url, sslmode='require')
cur = conn.cursor()

# Check token_transaction columns
cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'token_transaction' 
    ORDER BY ordinal_position
""")
rows = cur.fetchall()
print("=== token_transaction columns ===")
for row in rows:
    print(f"  {row[0]:30s} {row[1]}")

# Check group_file table exists
cur.execute("""
    SELECT EXISTS (
        SELECT FROM information_schema.tables 
        WHERE table_name = 'group_file'
    )
""")
print(f"\ngroup_file table exists: {cur.fetchone()[0]}")

# Check uploads/receipts folder path
print(f"\nuploads/receipts exists on disk: {os.path.exists('uploads/receipts')}")

cur.close()
conn.close()