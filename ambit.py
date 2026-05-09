import psycopg2

conn = psycopg2.connect(
    "postgresql://brainspark_db_user:R2GxoDz1o1rIHqPKNEr8UopoaFWyYyGz@dpg-d7mk61gsfn5c73djkee0-a.oregon-postgres.render.com/brainspark_db",
    sslmode="require"
)

cur = conn.cursor()

cur.execute('SELECT * FROM "user"')

users = cur.fetchall()

for user in users:
    print("\n-------------------")
    print("ID:", user[0])
    print("Name:", user[1])
    print("Username:", user[2])
    print("Email:", user[3])
    print("School:", user[4])
    print("Profession:", user[5])
    print("Level:", user[6])
    print("Country:", user[7])
    print("Profile Pic:", user[9])
    print("Verified:", user[11])

cur.close()
conn.close()