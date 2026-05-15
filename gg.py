from app import app
from models import MasteryProgress, db

with app.app_context():
    try:
        # Check if table exists
        count = MasteryProgress.query.count()
        print(f"✅ Table exists! Rows: {count}")
        
        # Show all columns
        cols = [c.name for c in MasteryProgress.__table__.columns]
        print(f"📋 Columns: {cols}")

    except Exception as e:
        print(f"❌ Table missing or error: {e}")
        print("👉 Running db.create_all() to create it...")
        try:
            db.create_all()
            print("✅ Done! Tables created. Run this script again to verify.")
        except Exception as e2:
            print(f"❌ Could not create tables: {e2}")