from app import app, db
from models import GroupFile  # make sure it's imported

with app.app_context():
    db.create_all()
    print("Done.")