"""
delete_fake_students.py
Run once:  python delete_fake_students.py

Deletes all fake seeded students (identified by password FakePass1!)
and keeps only 5 of them. Real user accounts are never touched.
"""

from app import app, db
from models import User
from werkzeug.security import check_password_hash

FAKE_PASSWORD = "FakePass1!"
KEEP = 5

def run():
    with app.app_context():
        all_users = User.query.all()

        # Identify fake users by their password hash
        fake_users = [u for u in all_users if u.password_hash and check_password_hash(u.password_hash, FAKE_PASSWORD)]

        print(f"Total users in DB      : {len(all_users):,}")
        print(f"Fake seeded users found: {len(fake_users):,}")
        print(f"Will keep              : {KEEP}")
        print(f"Will delete            : {max(0, len(fake_users) - KEEP):,}")

        if not fake_users:
            print("No fake users found. Nothing to do.")
            return

        # Keep the first 5, delete the rest
        to_keep   = fake_users[:KEEP]
        to_delete = fake_users[KEEP:]

        print(f"\nKeeping these {KEEP} accounts:")
        for u in to_keep:
            print(f"  - {u.username} ({u.email})")

        if not to_delete:
            print("\nNothing to delete.")
            return

        confirm = input(f"\nDelete {len(to_delete):,} fake users? Type YES to confirm: ").strip()
        if confirm != "YES":
            print("Aborted.")
            return

        # Delete in batches of 500 to avoid memory issues
        BATCH = 500
        deleted = 0
        ids_to_delete = [u.id for u in to_delete]

        for i in range(0, len(ids_to_delete), BATCH):
            batch_ids = ids_to_delete[i:i + BATCH]
            User.query.filter(User.id.in_(batch_ids)).delete(synchronize_session=False)
            db.session.commit()
            deleted += len(batch_ids)
            print(f"  Deleted {deleted:,} / {len(ids_to_delete):,}…")

        print(f"\n✅ Done! {deleted:,} fake users deleted. {KEEP} kept.")

if __name__ == "__main__":
    run()