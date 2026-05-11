# run once: python fix_pics.py
import os, sys
sys.path.insert(0, '.')
from app import app
from models import db, User

with app.app_context():
    for u in User.query.all():
        if u.profile_pic:
            path = os.path.join('uploads', 'profiles', u.profile_pic)
            if not os.path.exists(path):
                print(f"Cleared missing pic for {u.username}: {u.profile_pic}")
                u.profile_pic = None
    db.session.commit()
    print("Done.")