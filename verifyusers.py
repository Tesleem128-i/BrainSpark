from app import app
from models import db, User

with app.app_context():
    # Get all unverified users
    unverified = User.query.filter_by(is_verified=False).all()

    if not unverified:
        print("✅ No unverified users found.")

    else:
        print(f"Found {len(unverified)} unverified users:\n")

        for u in unverified:
            print(f"  - {u.name} (@{u.username}) — {u.email}")

        # Ask for confirmation
        confirm = input(f"\nVerify all {len(unverified)} users? (yes/no): ")

        if confirm.strip().lower() == "yes":

            # Verify each user
            for u in unverified:
                u.is_verified = True

            # Save changes
            db.session.commit()

            print("\n✅ All users verified successfully!")

        else:
            print("\n❌ Cancelled. No changes made.")