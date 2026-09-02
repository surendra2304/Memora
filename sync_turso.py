"""
Turso Cloud Direct Synchronizer
Syncs all tables, schemas, agents, namespaces, and memories to Turso Cloud DB.
"""
import os
import sqlite3
import requests
from dotenv import load_dotenv

load_dotenv()

TURSO_URL = os.getenv("TURSO_DATABASE_URL", "https://memora-db-surendra2304.aws-ap-south-1.turso.io")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

if not TURSO_TOKEN:
    print("[!] Error: TURSO_AUTH_TOKEN missing in .env")
    exit(1)

PIPELINE_URL = f"{TURSO_URL}/v2/pipeline"
HEADERS = {
    "Authorization": f"Bearer {TURSO_TOKEN}",
    "Content-Type": "application/json"
}

def execute_turso_sql(sql_query: str):
    payload = {
        "requests": [
            {"type": "execute", "stmt": {"sql": sql_query}}
        ]
    }
    resp = requests.post(PIPELINE_URL, headers=HEADERS, json=payload, timeout=15)
    data = resp.json()
    if "results" in data and len(data["results"]) > 0:
        res = data["results"][0]
        if res.get("type") == "error":
            return False, res.get("error", {}).get("message", "Unknown error")
    return True, data

def sync_database():
    local_db = "./data/memora.db"
    if not os.path.exists(local_db):
        print(f"[!] Local database {local_db} not found.")
        return

    conn = sqlite3.connect(local_db)
    cur = conn.cursor()

    print("[*] Fetching schema from local database...")
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables_ddl = [r[0] for r in cur.fetchall() if r[0]]

    print(f"[*] Creating {len(tables_ddl)} tables in Turso Cloud...")
    for ddl in tables_ddl:
        ok, msg = execute_turso_sql(ddl)
        if not ok:
            print(f"    [!] Warning on DDL: {msg}")

    tables_order = ["agents", "namespaces", "memory_records", "access_grants", "audit_logs"]
    for tbl in tables_order:
        try:
            cur.execute(f"SELECT * FROM {tbl}")
            rows = cur.fetchall()
            col_names = [d[0] for d in cur.description]
            print(f"[*] Syncing {len(rows)} records for table '{tbl}'...")

            for row in rows:
                cols_str = ", ".join(col_names)
                val_list = []
                for v in row:
                    if v is None:
                        val_list.append("NULL")
                    elif isinstance(v, (int, float)):
                        val_list.append(str(v))
                    else:
                        escaped = str(v).replace("'", "''")
                        val_list.append(f"'{escaped}'")
                vals_str = ", ".join(val_list)
                insert_sql = f"INSERT OR REPLACE INTO {tbl} ({cols_str}) VALUES ({vals_str});"
                ok, err = execute_turso_sql(insert_sql)
                if not ok:
                    print(f"    [!] Error inserting into {tbl}: {err}")
        except Exception as e:
            print(f"    [!] Skipped {tbl}: {e}")

    print("\n[+] Verification: Querying Turso Cloud memory count...")
    ok, res = execute_turso_sql("SELECT COUNT(*) FROM memory_records;")
    if ok:
        count = res["results"][0]["response"]["result"]["rows"][0][0]["value"]
        print(f"[SUCCESS] Turso Cloud now has {count} live memory records!")
    else:
        print("[!] Verification failed.")

if __name__ == "__main__":
    sync_database()
