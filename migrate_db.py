"""
Database migration script to add missing columns to existing SQLite tables.
This fixes schema mismatches when models are updated after the DB is created.
"""
import sqlite3
import os

DB_PATH = 'instance/knowitnow.db'

def column_exists(cursor, table, column):
    cursor.execute(f"PRAGMA table_info({table})")
    return any(col[1] == column for col in cursor.fetchall())

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    migrations = [
        # group_message table
        ("group_message", "message_type", "ALTER TABLE group_message ADD COLUMN message_type VARCHAR(20) DEFAULT 'text'"),
        ("group_message", "image_path", "ALTER TABLE group_message ADD COLUMN image_path VARCHAR(500)"),
        ("group_message", "poll_id", "ALTER TABLE group_message ADD COLUMN poll_id INTEGER"),

        # poll table
        ("poll", "is_active", "ALTER TABLE poll ADD COLUMN is_active BOOLEAN DEFAULT 1"),

        # brainstorm_note table
        ("brainstorm_note", "mentions", "ALTER TABLE brainstorm_note ADD COLUMN mentions TEXT"),
        ("brainstorm_note", "tags", "ALTER TABLE brainstorm_note ADD COLUMN tags TEXT"),
        ("brainstorm_note", "mention_ai", "ALTER TABLE brainstorm_note ADD COLUMN mention_ai BOOLEAN DEFAULT 0"),
        ("brainstorm_note", "image_path", "ALTER TABLE brainstorm_note ADD COLUMN image_path VARCHAR(500)"),
        ("brainstorm_note", "textbook_path", "ALTER TABLE brainstorm_note ADD COLUMN textbook_path VARCHAR(500)"),
        ("brainstorm_note", "solved_problem", "ALTER TABLE brainstorm_note ADD COLUMN solved_problem TEXT"),
        ("brainstorm_note", "has_media", "ALTER TABLE brainstorm_note ADD COLUMN has_media BOOLEAN DEFAULT 0"),

        # group_join_request table
        ("group_join_request", "responded_at", "ALTER TABLE group_join_request ADD COLUMN responded_at DATETIME"),

        # chat_group_member table
        ("chat_group_member", "role", "ALTER TABLE chat_group_member ADD COLUMN role VARCHAR(20) DEFAULT 'member'"),
    ]

    applied = 0
    for table, column, sql in migrations:
        try:
            if not column_exists(cursor, table, column):
                cursor.execute(sql)
                print(f"✅ Added column '{column}' to '{table}'")
                applied += 1
            else:
                print(f"⏭️  Column '{column}' already exists in '{table}'")
        except Exception as e:
            print(f"❌ Error adding '{column}' to '{table}': {e}")

    conn.commit()
    conn.close()
    print(f"\n🎉 Migration complete! {applied} column(s) added.")

if __name__ == '__main__':
    migrate()

