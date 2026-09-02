"""
Turso Cloud & Local Database Inspection CLI for Memora
"""
import os
import sys
import json
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import pandas as pd

pd.set_option("display.max_columns", None)
pd.set_option("display.max_rows", None)
pd.set_option("display.width", 1000)
pd.set_option("display.max_colwidth", None)

load_dotenv()

def get_engine():
    if len(sys.argv) > 1 and (sys.argv[1].endswith(".db") or sys.argv[1].endswith(".sqlite")):
        target_db = sys.argv[1]
        print(f"[*] Reading from local file: {target_db}")
        return create_engine(f"sqlite:///{target_db}")

    db_url = os.getenv("DATABASE_URL", "sqlite:///./data/memora.db")
    turso_token = os.getenv("TURSO_AUTH_TOKEN", "")

    if "turso.io" in db_url or db_url.startswith("libsql://"):
        clean_url = db_url.replace("libsql://", "sqlite+https://") if db_url.startswith("libsql://") else f"sqlite+{db_url}"
        if turso_token and "authToken" not in clean_url:
            clean_url = f"{clean_url}?authToken={turso_token}&secure=true"
        print("[*] Connecting to live Turso Cloud DB...")
        return create_engine(clean_url)

    print(f"[*] Connecting to database: {db_url}")
    return create_engine(db_url)

try:
    engine = get_engine()
except Exception as e:
    print(f"[!] Connection error: {e}")
    sys.exit(1)

def show_all_memories_clearly():
    query = """
        SELECT 
            COALESCE(a.name, 'unassigned') AS agent,
            n.path AS namespace,
            m.memory_type AS type,
            m.confidence AS conf,
            m.lifecycle_state AS state,
            m.created_at,
            m.content_text AS memory_content
        FROM memory_records m
        LEFT JOIN agents a ON m.owner_id = a.id
        LEFT JOIN namespaces n ON m.namespace_id = n.id
        ORDER BY m.created_at DESC;
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        
        print("\n" + "="*90)
        print(f"ALL STORED MEMORIES ACROSS ALL AGENTS (TOTAL: {len(df)})")
        print("="*90)
        
        for idx, row in df.iterrows():
            print(f"\n[{idx+1}] AGENT: {row['agent'].upper()} | TYPE: {row['type']} | CONFIDENCE: {row['conf']} | STATE: {row['state']}")
            print(f"    Namespace : {row['namespace']}")
            print(f"    Timestamp : {row['created_at']}")
            print(f"    Memory    : {row['memory_content'].strip()}")
            print("-" * 90)

def show_events(limit=25):
    query = f"""
        SELECT 
            COALESCE(a.name, 'SYSTEM') AS agent,
            l.action,
            l.timestamp,
            l.details
        FROM audit_logs l
        LEFT JOIN agents a ON l.actor_id = a.id
        ORDER BY l.timestamp DESC
        LIMIT {limit};
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        print("\n" + "="*90)
        print(f"RECENT EVENTS & SECURITY AUDIT LOGS (LAST {len(df)})")
        print("="*90)
        for idx, row in df.iterrows():
            details = row['details']
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except Exception:
                    pass
            
            allowed = details.get("allowed") if isinstance(details, dict) else "N/A"
            rule = details.get("rule_matched") if isinstance(details, dict) else "N/A"
            reason = details.get("reason") if isinstance(details, dict) else str(details)

            print(f"\n[{idx+1}] ACTION: {row['action'].upper()} | AGENT: {row['agent'].upper()} | ALLOWED: {allowed}")
            print(f"    Rule Matched : {rule}")
            print(f"    Timestamp    : {row['timestamp']}")
            print(f"    Reason       : {reason}")
            print("-" * 90)

def show_agent_summary():
    query = """
        SELECT 
            COALESCE(a.name, 'unassigned') AS agent,
            COUNT(m.id) AS total_memories,
            MIN(m.created_at) AS first_seen,
            MAX(m.created_at) AS last_updated
        FROM memory_records m
        LEFT JOIN agents a ON m.owner_id = a.id
        GROUP BY a.name
        ORDER BY total_memories DESC;
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        print("\n" + "="*70)
        print("MEMORY COUNT BREAKDOWN BY AGENT")
        print("="*70)
        print(df.to_string(index=False))
        print("="*70)

def print_summary():
    print("\n" + "="*50)
    print("DATABASE SUMMARY")
    print("="*50)
    tables = [
        "agents", "namespaces", "memory_records",
        "access_grants", "audit_logs"
    ]
    with engine.connect() as conn:
        for tbl in tables:
            try:
                cnt = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
                print(f"  {tbl:<22}: {cnt} rows")
            except Exception:
                print(f"  {tbl:<22}: (table not found)")

def show_agents():
    print("\n" + "="*70)
    print("REGISTERED AGENTS")
    print("="*70)
    with engine.connect() as conn:
        df = pd.read_sql(text("SELECT id, name, role, bounded_scope, created_at FROM agents;"), conn)
        print(df.to_string(index=False))

def show_namespaces():
    print("\n" + "="*70)
    print("NAMESPACES")
    print("="*70)
    with engine.connect() as conn:
        df = pd.read_sql(text("SELECT id, path, type, created_at FROM namespaces;"), conn)
        print(df.to_string(index=False))

def interactive_shell():
    print("\n" + "="*50)
    print("INTERACTIVE SQL SHELL (type 'exit' to quit)")
    print("="*50)
    with engine.connect() as conn:
        while True:
            try:
                q = input("\nturso-sql> ").strip()
                if not q or q.lower() in ["exit", "quit", "q"]:
                    break
                df = pd.read_sql(text(q), conn)
                print(df.to_string(index=False))
            except Exception as e:
                print(f"SQL Error: {e}")

if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "memories"
    
    if arg in ["events", "audit", "logs"]:
        show_events(25)
    elif arg == "agents":
        show_agents()
    elif arg == "namespaces":
        show_namespaces()
    elif arg == "summary":
        show_agent_summary()
    elif arg == "status":
        print_summary()
    elif arg in ["shell", "sql"]:
        interactive_shell()
    else:
        show_all_memories_clearly()
        show_agent_summary()
