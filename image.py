import psycopg2
import os
from PIL import Image

conn = psycopg2.connect(
    "postgresql://brainspark_db_user:R2GxoDz1o1rIHqPKNEr8UopoaFWyYyGz@dpg-d7mk61gsfn5c73djkee0-a.oregon-postgres.render.com/brainspark_db",
    sslmode="require"
)

cur = conn.cursor()

# get profile pictures
cur.execute('SELECT username, profile_pic FROM "user"')

users = cur.fetchall()

for username, pic in users:
    if pic:  # make sure image exists
        path = os.path.join("uploads/profiles", pic)

        print(f"Opening {username}'s image -> {path}")

        if os.path.exists(path):
            img = Image.open(path)
            img.show()
        else:
            print("Image not found:", path)

cur.close()
conn.close()