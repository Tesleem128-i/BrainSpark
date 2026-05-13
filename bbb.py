import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

db_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_UR')

if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

# psycopg2 needs no sslmode in the URL, pass it as param
if '?' in db_url:
    db_url = db_url.split('?')[0]

conn = psycopg2.connect(db_url, sslmode='require')
cur = conn.cursor()

cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'token_transaction' 
    ORDER BY ordinal_position
""")

rows = cur.fetchall()

print("\n=== token_transaction columns ===")
for row in rows:
    print(f"  {row[0]:30s} {row[1]}")

has_ref = any(row[0] == 'reference_code' for row in rows)
print(f"\nreference_code exists: {has_ref}")

cur.close()
conn.close()