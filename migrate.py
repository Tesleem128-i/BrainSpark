import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

db_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_UR')

if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)
if '?' in db_url:
    db_url = db_url.split('?')[0]
db_url += '?sslmode=require'

engine = create_engine(db_url)

with engine.connect() as conn:
    conn.execute(text('ALTER TABLE "user" ADD COLUMN IF NOT EXISTS push_subscription TEXT'))
    conn.commit()
    print("Done! push_subscription column added.")