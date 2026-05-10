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
    """
Complete database migration script.
Adds all missing columns and tables for the updated Brainspark schema.
Run with: python migrate.py
"""
import sqlite3
import os

DB_PATH = 'instance/knowitnow.db'


def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(col[1] == column for col in cursor.fetchall())


def table_exists(cursor, table):
    cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def migrate():
    if not os.path.exists(DB_PATH):
        print(f"❌ Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    applied = 0

    # ── NEW TABLES ────────────────────────────────────────────────────────────

    new_tables = {
        'generated_question': """
            CREATE TABLE generated_question (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                question_text TEXT NOT NULL,
                options TEXT NOT NULL,
                correct_answer TEXT NOT NULL,
                explanation TEXT,
                source_hash VARCHAR(64),
                difficulty VARCHAR(20),
                question_type VARCHAR(20),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES user (id)
            )""",

        'topic_mastery': """
            CREATE TABLE topic_mastery (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                topic VARCHAR(200) NOT NULL,
                total_questions INTEGER DEFAULT 0,
                correct_answers INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0,
                last_score REAL DEFAULT 0.0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES user (id),
                UNIQUE (user_id, topic)
            )""",

        'wrong_answer': """
            CREATE TABLE wrong_answer (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                topic VARCHAR(200) NOT NULL,
                question_text TEXT NOT NULL,
                correct_answer VARCHAR(10),
                user_answer VARCHAR(10),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES user (id)
            )""",

        'app_notification': """
            CREATE TABLE app_notification (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                notif_type VARCHAR(40) NOT NULL,
                title VARCHAR(200) NOT NULL,
                body TEXT,
                link_type VARCHAR(30),
                link_id INTEGER,
                is_read BOOLEAN DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES user (id)
            )""",
    }

    for table_name, ddl in new_tables.items():
        if not table_exists(cursor, table_name):
            cursor.execute(ddl)
            print(f"✅ Created table '{table_name}'")
            applied += 1
        else:
            print(f"⏭️  Table '{table_name}' already exists")

    # ── COLUMN ADDITIONS ──────────────────────────────────────────────────────
    # Format: (table, column, alter_sql)

    migrations = [

        # ── user ──────────────────────────────────────────────────────────────
        ("user", "push_subscription",
         "ALTER TABLE user ADD COLUMN push_subscription TEXT"),

        # ── chat_group_member ─────────────────────────────────────────────────
        ("chat_group_member", "role",
         "ALTER TABLE chat_group_member ADD COLUMN role VARCHAR(20) DEFAULT 'member'"),
        ("chat_group_member", "is_muted",
         "ALTER TABLE chat_group_member ADD COLUMN is_muted BOOLEAN DEFAULT 0"),

        # ── group_message ─────────────────────────────────────────────────────
        ("group_message", "message_type",
         "ALTER TABLE group_message ADD COLUMN message_type VARCHAR(20) DEFAULT 'text'"),
        ("group_message", "image_path",
         "ALTER TABLE group_message ADD COLUMN image_path VARCHAR(500)"),
        ("group_message", "pdf_path",
         "ALTER TABLE group_message ADD COLUMN pdf_path VARCHAR(500)"),
        ("group_message", "voice_path",
         "ALTER TABLE group_message ADD COLUMN voice_path VARCHAR(500)"),
        ("group_message", "poll_id",
         "ALTER TABLE group_message ADD COLUMN poll_id INTEGER"),
        ("group_message", "reply_to_id",
         "ALTER TABLE group_message ADD COLUMN reply_to_id INTEGER REFERENCES group_message(id)"),
        ("group_message", "mentions",
         "ALTER TABLE group_message ADD COLUMN mentions TEXT"),
        ("group_message", "reactions",
         "ALTER TABLE group_message ADD COLUMN reactions TEXT"),
        ("group_message", "is_edited",
         "ALTER TABLE group_message ADD COLUMN is_edited BOOLEAN DEFAULT 0"),
        ("group_message", "is_deleted",
         "ALTER TABLE group_message ADD COLUMN is_deleted BOOLEAN DEFAULT 0"),
        ("group_message", "edited_at",
         "ALTER TABLE group_message ADD COLUMN edited_at DATETIME"),

        # ── poll ──────────────────────────────────────────────────────────────
        ("poll", "is_active",
         "ALTER TABLE poll ADD COLUMN is_active BOOLEAN DEFAULT 1"),

        # ── brainstorm_session ────────────────────────────────────────────────
        ("brainstorm_session", "whiteboard_data",
         "ALTER TABLE brainstorm_session ADD COLUMN whiteboard_data TEXT"),
        ("brainstorm_session", "shared_doc",
         "ALTER TABLE brainstorm_session ADD COLUMN shared_doc TEXT"),

        # ── brainstorm_note ───────────────────────────────────────────────────
        ("brainstorm_note", "color",
         "ALTER TABLE brainstorm_note ADD COLUMN color VARCHAR(20) DEFAULT '#ff4f30'"),
        ("brainstorm_note", "mentions",
         "ALTER TABLE brainstorm_note ADD COLUMN mentions TEXT"),
        ("brainstorm_note", "tags",
         "ALTER TABLE brainstorm_note ADD COLUMN tags TEXT"),
        ("brainstorm_note", "mention_ai",
         "ALTER TABLE brainstorm_note ADD COLUMN mention_ai BOOLEAN DEFAULT 0"),
        ("brainstorm_note", "image_path",
         "ALTER TABLE brainstorm_note ADD COLUMN image_path VARCHAR(500)"),
        ("brainstorm_note", "textbook_path",
         "ALTER TABLE brainstorm_note ADD COLUMN textbook_path VARCHAR(500)"),
        ("brainstorm_note", "solved_problem",
         "ALTER TABLE brainstorm_note ADD COLUMN solved_problem TEXT"),
        ("brainstorm_note", "has_media",
         "ALTER TABLE brainstorm_note ADD COLUMN has_media BOOLEAN DEFAULT 0"),
        ("brainstorm_note", "upvotes",
         "ALTER TABLE brainstorm_note ADD COLUMN upvotes INTEGER DEFAULT 0"),

        # ── group_join_request ────────────────────────────────────────────────
        ("group_join_request", "responded_at",
         "ALTER TABLE group_join_request ADD COLUMN responded_at DATETIME"),
    ]

    for table, column, sql in migrations:
        if not table_exists(cursor, table):
            print(f"⚠️  Table '{table}' does not exist — skipping column '{column}'")
            continue
        try:
            if not column_exists(cursor, table, column):
                cursor.execute(sql)
                print(f"✅ Added '{column}' to '{table}'")
                applied += 1
            else:
                print(f"⏭️  '{column}' already exists in '{table}'")
        except Exception as e:
            print(f"❌ Error adding '{column}' to '{table}': {e}")

    conn.commit()
    conn.close()
    print(f"\n🎉 Migration complete! {applied} change(s) applied.")


if __name__ == '__main__':
    migrate()