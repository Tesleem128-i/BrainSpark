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

migrations = [

    # ── user ──────────────────────────────────────────────────────────────────
    'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS push_subscription TEXT',

    # ── chat_group_member ─────────────────────────────────────────────────────
    'ALTER TABLE chat_group_member ADD COLUMN IF NOT EXISTS role VARCHAR(20) DEFAULT \'member\'',
    'ALTER TABLE chat_group_member ADD COLUMN IF NOT EXISTS is_muted BOOLEAN DEFAULT FALSE',

    # ── group_message ─────────────────────────────────────────────────────────
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS message_type VARCHAR(20) DEFAULT \'text\'',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS image_path VARCHAR(500)',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS pdf_path VARCHAR(500)',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS voice_path VARCHAR(500)',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS poll_id INTEGER',
    'ALTER TABLE group_message ADD COLUMN IF NOT EXISTS reply_to_id INTEGER REFERENCES group_message(id)',
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

    # ── brainstorm_note ───────────────────────────────────────────────────────
    'ALTER TABLE brainstorm_note ADD COLUMN IF NOT EXISTS color VARCHAR(20) DEFAULT \'#ff4f30\'',
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

    # ── new tables ────────────────────────────────────────────────────────────
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
    )""",

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
    )""",

    """
    CREATE TABLE IF NOT EXISTS wrong_answer (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES "user"(id),
        topic VARCHAR(200) NOT NULL,
        question_text TEXT NOT NULL,
        correct_answer VARCHAR(10),
        user_answer VARCHAR(10),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""",

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
    )""",
]

with engine.connect() as conn:
    for sql in migrations:
        try:
            conn.execute(text(sql))
            conn.commit()
            # Print just the first line so output is readable
            print(f"✅ {sql.strip().splitlines()[0][:80]}")
        except Exception as e:
            conn.rollback()
            print(f"❌ {sql.strip().splitlines()[0][:80]}")
            print(f"   Error: {e}")

print("\n🎉 PostgreSQL migration complete!")