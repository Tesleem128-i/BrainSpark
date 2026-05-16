import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

db_url = os.getenv('DATABASE_URL') or os.getenv('DATABASE_UR')

if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)

conn = psycopg2.connect(db_url)
conn.autocommit = True
cur = conn.cursor()

print("Resetting all stats and tokens...\n")

# 1. Delete all token transactions (clears revenue, profit, api cost, tokens issued, pending)
cur.execute("DELETE FROM token_transaction;")
print("✅ All token transactions deleted (revenue, profit, pending all → 0)")

# 2. Reset ALL users spark_tokens to 0
cur.execute("UPDATE \"user\" SET spark_tokens = 0;")
print("✅ All users spark tokens → 0")

# 3. Reset ALL users total_spent and total_tokens_purchased to 0
cur.execute("UPDATE \"user\" SET total_spent = 0;")
cur.execute("UPDATE \"user\" SET total_tokens_purchased = 0;")
print("✅ All users total_spent and total_tokens_purchased → 0")

# 4. Reset admin token_pool to 0
cur.execute("UPDATE \"user\" SET token_pool = 0 WHERE username = (SELECT username FROM \"user\" WHERE token_pool > 0 LIMIT 1);")
print("✅ Admin token_pool → 0 (tokens available → 0)")

cur.close()
conn.close()

print("\n✅ Done! All stats are now at zero.")