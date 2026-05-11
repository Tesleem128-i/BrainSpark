"""
identify_fake_users.py
Run with: python identify_fake_users.py
Identifies fake/seeded users vs real users and prints 5 fake user samples.
"""

from werkzeug.security import check_password_hash
from app import app, db
from models import User

FAKE_PASSWORD = "FakePass1!"

def run():
    with app.app_context():
        all_users = User.query.all()
        
        fake_users = []
        real_users = []
        
        for user in all_users:
            try:
                if check_password_hash(user.password_hash, FAKE_PASSWORD):
                    fake_users.append(user)
                else:
                    real_users.append(user)
            except Exception:
                real_users.append(user)  # If check fails, treat as real
        
        print(f"\n📊 User Summary:")
        print(f"   Total users : {len(all_users):,}")
        print(f"   Fake users  : {len(fake_users):,}")
        print(f"   Real users  : {len(real_users):,}")
        
        print(f"\n🤖 Sample Fake Users (password for all: '{FAKE_PASSWORD}'):")
        print("-" * 60)
        for user in fake_users[:5]:
            print(f"   Name     : {user.name}")
            print(f"   Username : {user.username}")
            print(f"   Email    : {user.email}")
            print(f"   Password : {FAKE_PASSWORD}")
            print(f"   School   : {user.school}")
            print(f"   Country  : {user.country}")
            print("-" * 60)
        
        print(f"\n✅ Real Users ({len(real_users):,} total):")
        for user in real_users[:5]:
            print(f"   Name     : {user.name}")
            print(f"   Username : {user.username}")
            print(f"   Email    : {user.email}")
            print(f"   Password : [hidden - set by user]")
            print("-" * 60)

if __name__ == "__main__":
    run()