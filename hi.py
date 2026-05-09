import psycopg2

conn = psycopg2.connect(
    "postgresql://brainspark_db_user:R2GxoDz1o1rIHqPKNEr8UopoaFWyYyGz@dpg-d7mk61gsfn5c73djkee0-a.oregon-postgres.render.com/brainspark_db",
    sslmode="require"
)

cur = conn.cursor()

cur.execute('DELETE FROM "user" WHERE is_verified = FALSE')

conn.commit()

print("Unverified users deleted successfully!")

cur.close()
conn.close()