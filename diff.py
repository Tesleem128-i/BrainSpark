from dotenv import load_dotenv
load_dotenv()

from app import app, db
from models import User
from werkzeug.security import check_password_hash

FAKE_PASSWORD = "FakePass1!"

def run():
    with app.app_context():
        print("Connecting to database...")
        
        # Count first — fast single query
        total = User.query.count()
        print(f"Total users in DB: {total:,}")
        
        # Get just the first 100 to find the fake hash
        print("Finding fake hash from sample...")
        sample = User.query.limit(100).all()
        
        fake_hash = None
        for u in sample:
            if u.password_hash and check_password_hash(u.password_hash, FAKE_PASSWORD):
                fake_hash = u.password_hash
                print(f"Found fake hash!")
                break
        
        if not fake_hash:
            print("No fake users found in first 100 — all may be real.")
            return
        
        # Now count using SQL — no need to load 50k objects into memory
        print("Counting fake vs real users...")
        fake_count = User.query.filter(User.password_hash == fake_hash).count()
        real_count = total - fake_count
        
        print(f"\n📊 User Summary:")
        print(f"   Total users : {total:,}")
        print(f"   Fake users  : {fake_count:,}")
        print(f"   Real users  : {real_count:,}")
        
        # Sample fake users
        fake_samples = User.query.filter(User.password_hash == fake_hash).limit(5).all()
        print(f"\n🤖 Sample Fake Users:")
        print("-" * 60)
        for user in fake_samples:
            print(f"   Name     : {user.name}")
            print(f"   Username : {user.username}")
            print(f"   Email    : {user.email}")
            print(f"   Password : {FAKE_PASSWORD}")
            print(f"   School   : {user.school}")
            print(f"   Country  : {user.country}")
            print("-" * 60)
        
        # Sample real users
        real_samples = User.query.filter(User.password_hash != fake_hash).limit(5).all()
        print(f"\n✅ Sample Real Users:")
        print("-" * 60)
        for user in real_samples:
            print(f"   Name     : {user.name}")
            print(f"   Username : {user.username}")
            print(f"   Email    : {user.email}")
            print(f"   Password : [hidden]")
            print("-" * 60)

if __name__ == "__main__":
    run()