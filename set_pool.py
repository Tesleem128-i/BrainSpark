from app import app, db
from models import User

with app.app_context():
    admin = User.query.filter_by(username='Peace1').first()
    if admin:
        admin.token_pool = 10000
        db.session.commit()
        print(f"✅ Done! token_pool set to {admin.token_pool} for {admin.username}")
    else:
        print("❌ User 'Peace1' not found")