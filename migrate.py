"""
PostgreSQL migration script for Brainspark on Render.
Run with: python migrate_postgres.py
"""
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

db_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_UR')

if not db_url:
    raise RuntimeError("No DATABASE_URL found in environment variables.")

if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)
if '?' in db_url:
    db_url = db_url.split('?')[0]
db_url += '?sslmode=require'

engine = create_engine(db_url)

# ── String-based migrations ──────────────────────────────────────────────────
string_migrations = [

    # ── user ──────────────────────────────────────────────────────────────────
    'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS push_subscription TEXT',
    'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS bio VARCHAR(160)',
    'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS spark_tokens INTEGER DEFAULT 0',
    'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS total_tokens_purchased INTEGER DEFAULT 0',
    'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS total_spent REAL DEFAULT 0.0',

    # ── chat_group_member ─────────────────────────────────────────────────────
    "ALTER TABLE chat_group_member ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT 'member'",
    'ALTER TABLE chat_group_member ADD COLUMN IF NOT EXISTS is_muted BOOLEAN DEFAULT FALSE',

    # ── message ───────────────────────────────────────────────────────────────
    'ALTER TABLE message ADD COLUMN IF NOT EXISTS image_path VARCHAR(500)',

    # ── group_message ─────────────────────────────────────────────────────────
    "ALTER TABLE group_message ADD COLUMN IF NOT EXISTS message_type VARCHAR(20) DEFAULT 'text'",
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS image_path VARCHAR(500)',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS pdf_path VARCHAR(500)',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS voice_path VARCHAR(500)',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS poll_id INTEGER',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS reply_to_id INTEGER',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS mentions TEXT',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS reactions TEXT',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS is_edited BOOLEAN DEFAULT FALSE',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP',

    # ── poll ──────────────────────────────────────────────────────────────────
    'ALTER TABLE poll ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE',

    # ── brainstorm_session ────────────────────────────────────────────────────
    'ALTER TABLE brainstorm_session ADD COLUMN IF NOT EXISTS whiteboard_data TEXT',
    'ALTER TABLE brainstorm_session ADD COLUMN IF NOT EXISTS shared_doc TEXT',
    'ALTER TABLE brainstorm_session ADD COLUMN IF NOT EXISTS teacher_id INTEGER',

    # ── brainstorm_note ───────────────────────────────────────────────────────
    "ALTER TABLE brainstorm_note ADD COLUMN IF NOT EXISTS color VARCHAR(20) DEFAULT '#ff4f30'",
    'ALTER TABLE brainstorm_note ADD COLUMN IF NOT EXISTS mentions TEXT',
    'ALTER TABLE brainstorm_note ADD COLUMN IF NOT EXISTS tags TEXT',
    'ALTER TABLE brainstorm_note ADD COLUMN IF NOT EXISTS mention_ai BOOLEAN DEFAULT FALSE',
    'ALTER TABLE brainstorm_note ADD COLUMN IF NOT EXISTS image_path VARCHAR(500)',
    'ALTER TABLE brainstorm_note ADD COLUMN IF NOT EXISTS textbook_path VARCHAR(500)',
    'ALTER TABLE brainstorm_note ADD COLUMN IF NOT EXISTS solved_problem TEXT',
    'ALTER TABLE brainstorm_note ADD COLUMN IF NOT EXISTS has_media BOOLEAN DEFAULT FALSE',
    'ALTER TABLE brainstorm_note ADD COLUMN IF NOT EXISTS upvotes INTEGER DEFAULT 0',

    # ── group_join_request ────────────────────────────────────────────────────
    'ALTER TABLE group_join_request ADD COLUMN IF NOT EXISTS responded_at TIMESTAMP',

    # ── token_transaction ─────────────────────────────────────────────────────
    'ALTER TABLE token_transaction ADD COLUMN IF NOT EXISTS reference_code VARCHAR(20)',
    'ALTER TABLE token_transaction ADD COLUMN IF NOT EXISTS verified_by INTEGER',
    'ALTER TABLE token_transaction ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP',
    'ALTER TABLE token_transaction ADD COLUMN IF NOT EXISTS platform_fee REAL DEFAULT 500',
]

# ── NEW TABLES (CREATE IF NOT EXISTS) ────────────────────────────────────────
table_migrations = [
    """
    CREATE TABLE IF NOT EXISTS group_file (
        id SERIAL PRIMARY KEY,
        filename VARCHAR(300) NOT NULL,
        file_data TEXT NOT NULL,
        mime_type VARCHAR(100),
        file_type VARCHAR(20),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS generated_question (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES "user"(id),
        question_text TEXT NOT NULL,
        options TEXT NOT NULL,
        correct_answer TEXT NOT NULL,
        explanation TEXT,
        source_hash VARCHAR(64),
        difficulty VARCHAR(20),
        question_type VARCHAR(20),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS topic_mastery (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES "user"(id),
        topic VARCHAR(200) NOT NULL,
        total_questions INTEGER DEFAULT 0,
        correct_answers INTEGER DEFAULT 0,
        attempts INTEGER DEFAULT 0,
        last_score REAL DEFAULT 0.0,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, topic)
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS wrong_answer (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES "user"(id),
        topic VARCHAR(200) NOT NULL,
        question_text TEXT NOT NULL,
        correct_answer VARCHAR(10),
        user_answer VARCHAR(10),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS app_notification (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES "user"(id),
        notif_type VARCHAR(40) NOT NULL,
        title VARCHAR(200) NOT NULL,
        body TEXT,
        link_type VARCHAR(30),
        link_id INTEGER,
        is_read BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS hand_raise (
        id SERIAL PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES brainstorm_session(id),
        user_id INTEGER NOT NULL REFERENCES "user"(id),
        status VARCHAR(20) DEFAULT 'raised',
        question_text TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        answered_at TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS token_transaction (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES "user"(id),
        amount_paid REAL NOT NULL,
        platform_fee REAL DEFAULT 500,
        tokens_added INTEGER NOT NULL,
        receipt_path VARCHAR(500),
        status VARCHAR(20) DEFAULT 'pending',
        reference_code VARCHAR(20),
        verified_by INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        verified_at TIMESTAMP
    )
    """,

    """
    CREATE TABLE IF NOT EXISTS token_usage_log (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES "user"(id),
        feature VARCHAR(100),
        tokens_used INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]

# ── COMBINE ALL ───────────────────────────────────────────────────────────────
all_migrations = string_migrations + table_migrations

print("Running PostgreSQL migrations...")
print(f"Database: {db_url.split('@')[-1].split('/')[0] if '@' in db_url else 'local'}\n")

ok_count = 0
skip_count = 0
error_count = 0

with engine.connect() as conn:
    conn.execution_options(isolation_level="AUTOCOMMIT")

    for i, sql in enumerate(all_migrations, 1):
        first_line = sql.strip().splitlines()[0][:80]
        try:
            conn.execute(text(sql))
            print(f"OK    [{i:2d}] {first_line}")
            ok_count += 1
        except Exception as e:
            err = str(e).strip()
            if 'already exists' in err or 'duplicate column' in err.lower():
                print(f"SKIP  [{i:2d}] {first_line}")
                skip_count += 1
            else:
                print(f"ERROR [{i:2d}] {first_line}")
                print(f"       {err[:120]}")
                error_count += 1

print(f"\nMigration complete!")
print(f"  OK:     {ok_count}")
print(f"  Skipped:{skip_count}")
print(f"  Errors: {error_count}")

if error_count == 0:
    print("\nAll good! Your database is up to date.")
else:
    print(f"\n{error_count} error(s) above need attention.")