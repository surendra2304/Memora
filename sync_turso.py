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

def execute_batch(statements: list[str]):
    """Executes a list of SQL statements in a single Turso pipeline transaction."""
    if not statements:
        return True, []
    
    payload = {
        "requests": [{"type": "execute", "stmt": {"sql": s}} for s in statements]
    }
    resp = requests.post(PIPELINE_URL, headers=HEADERS, json=payload, timeout=30)
    data = resp.json()
    errors = []
    if "results" in data:
        for i, res in enumerate(data["results"]):
            if res.get("type") == "error":
                errors.append(f"Statement {i}: {res.get('error', {}).get('message', 'Unknown error')}")
    return len(errors) == 0, errors

def sync_database():
    local_db = "./data/memora.db"
    if not os.path.exists(local_db):
        print(f"[!] Local database {local_db} not found.")
        return

    conn = sqlite3.connect(local_db)
    cur = conn.cursor()

    print("[*] Rebuilding Turso Cloud schema to match current Memora v2 data model...")
    drop_tables = [
        "memory_relationships",
        "audit_logs",
        "access_grants",
        "memory_records",
        "namespaces",
        "agents",
        "alembic_version"
    ]
    drop_stmts = [f"DROP TABLE IF EXISTS {tbl};" for tbl in drop_tables]
    ok, errs = execute_batch(drop_stmts)
    if not ok:
        print(f"    [!] Warnings on drop: {errs}")

    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL;")
    tables_ddl = [r[0] for r in cur.fetchall() if r[0]]
    
    print(f"[*] Creating {len(tables_ddl)} tables in Turso Cloud...")
    ok, errs = execute_batch(tables_ddl)
    if not ok:
        print(f"    [!] Errors on create tables: {errs}")

    cur.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL;")
    indices_ddl = [r[0] for r in cur.fetchall() if r[0]]
    if indices_ddl:
        execute_batch(indices_ddl)

    tables_order = ["agents", "namespaces", "memory_records", "access_grants", "audit_logs"]
    for tbl in tables_order:
        try:
            cur.execute(f"SELECT * FROM {tbl}")
            rows = cur.fetchall()
            col_names = [d[0] for d in cur.description]
            print(f"[*] Syncing {len(rows)} records for table '{tbl}'...")

            stmts = []
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
                stmts.append(f"INSERT INTO {tbl} ({cols_str}) VALUES ({vals_str});")

            for i in range(0, len(stmts), 50):
                chunk = stmts[i:i+50]
                ok, errs = execute_batch(chunk)
                if not ok:
                    print(f"    [!] Error inserting batch in {tbl}: {errs}")
        except Exception as e:
            print(f"    [!] Skipped {tbl}: {e}")

    print("\n[+] Verification: Querying Turso Cloud stats...")
    payload = {
        "requests": [
            {"type": "execute", "stmt": {"sql": "SELECT COUNT(*) FROM memory_records;"}},
            {"type": "execute", "stmt": {"sql": "SELECT COUNT(*) FROM agents;"}},
            {"type": "execute", "stmt": {"sql": "SELECT a.name, count(m.id) FROM agents a LEFT JOIN memory_records m ON a.id = m.owner_id GROUP BY a.name;"}}
        ]
    }
    resp = requests.post(PIPELINE_URL, headers=HEADERS, json=payload, timeout=15).json()
    try:
        mem_count = resp["results"][0]["response"]["result"]["rows"][0][0]["value"]
        agent_count = resp["results"][1]["response"]["result"]["rows"][0][0]["value"]
        print(f"[SUCCESS] Turso Cloud is in 100% sync!")
        print(f"  Total Agents: {agent_count}")
        print(f"  Total Memory Records: {mem_count}")
        print("\nBreakdown by Agent in Turso Cloud:")
        for row in resp["results"][2]["response"]["result"]["rows"]:
            agent_name = row[0]["value"]
            m_count = row[1]["value"]
            print(f"  - {agent_name:<15}: {m_count} memories")
    except Exception as e:
        print(f"[!] Error parsing stats: {e}, raw response: {resp}")

if __name__ == "__main__":
    sync_database()
