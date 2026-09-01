"""
Turso Cloud Database Inspection CLI for Memora
"""
import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
import pandas as pd

load_dotenv()

db_url = os.getenv("DATABASE_URL", "")
auth_token = os.getenv("TURSO_AUTH_TOKEN", "")

if not db_url:
    print("[!] Error: DATABASE_URL not set in .env")
    sys.exit(1)

if db_url.startswith("libsql://"):
    sync_url = f"sqlite+{db_url}?authToken={auth_token}&secure=true"
else:
    sync_url = db_url

try:
    engine = create_engine(sync_url)
except Exception as e:
    print(f"[!] Connection error: {e}")
    sys.exit(1)

def print_summary():
    print("\n" + "="*50)
    print("TURSO DATABASE SUMMARY")
    print("="*50)
    tables = [
        "agents", "namespaces", "memory_records",
        "access_grants", "audit_logs",
        "canonical_entities", "entity_mentions"
    ]
    with engine.connect() as conn:
        for tbl in tables:
            try:
                cnt = conn.execute(text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
                print(f"  {tbl:<22}: {cnt} rows")
            except Exception:
                print(f"  {tbl:<22}: (table not found)")

def show_agents():
    print("\n" + "="*50)
    print("REGISTERED AGENTS")
    print("="*50)
    with engine.connect() as conn:
        df = pd.read_sql(text("SELECT id, name, role, bounded_scope, created_at FROM agents;"), conn)
        print(df.to_string(index=False))

def show_namespaces():
    print("\n" + "="*50)
    print("NAMESPACES")
    print("="*50)
    with engine.connect() as conn:
        df = pd.read_sql(text("SELECT id, path, type, created_at FROM namespaces;"), conn)
        print(df.to_string(index=False))

def show_recent_memories(limit=10):
    print("\n" + "="*50)
    print(f"RECENT MEMORIES (LAST {limit})")
    print("="*50)
    with engine.connect() as conn:
        df = pd.read_sql(text(f"SELECT id, content_text, memory_type, confidence, lifecycle_state, created_at FROM memory_records ORDER BY created_at DESC LIMIT {limit};"), conn)
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
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"
    
    if arg == "agents":
        show_agents()
    elif arg == "namespaces":
        show_namespaces()
    elif arg == "memories":
        show_recent_memories()
    elif arg == "shell" or arg == "sql":
        interactive_shell()
    else:
        print_summary()
        show_agents()
        show_namespaces()
        show_recent_memories(5)
        print("\n[Tip] Run 'python db_viewer.py sql' to open an interactive SQL terminal!")
