"""
Inspect real data across all 9 agents in FRIDAY Universe
"""
import os
import sqlite3
import json
import glob

base = 'd:/FRIDAY Universe'

def inspect_db(db_path, title):
    if not os.path.exists(db_path):
        print(f"[-] {title}: {db_path} does not exist")
        return
    print(f"\n=== {title} ({db_path}) ===")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in c.fetchall()]
    print("  Tables:", tables)
    for t in tables:
        try:
            c.execute(f"SELECT count(*) FROM {t}")
            cnt = c.fetchone()[0]
            print(f"    Table '{t}': {cnt} rows")
            if cnt > 0:
                c.execute(f"SELECT * FROM {t} LIMIT 3")
                rows = c.fetchall()
                col_names = [d[0] for d in c.description]
                print(f"      Columns: {col_names}")
                for row in rows:
                    print(f"      Row: {str(row)[:120]}...")
        except Exception as e:
            print(f"    Error reading {t}: {e}")
    conn.close()

# 1. FRIDAY
inspect_db(os.path.join(base, 'FRIDAY/data/friday.db'), 'FRIDAY Database')

# 2. Forge
inspect_db(os.path.join(base, 'Forge/data/forge.db'), 'Forge Database')

# 3. Sentinel
inspect_db(os.path.join(base, 'Sentinel/data/live_test.db'), 'Sentinel Database')

# 4. Inference
inspect_db(os.path.join(base, 'Inference/data/universe.db'), 'Inference Database')

# 5. Cortex
inspect_db(os.path.join(base, 'Cortex/data/cortex.db'), 'Cortex Database')

# 6. IntelX
inspect_db(os.path.join(base, 'IntelX/data/intelx.db'), 'IntelX Database')

# 7. Futuris
inspect_db(os.path.join(base, 'Futuris/data/futuris.db'), 'Futuris Database')

# 8. Stratex
print("\n=== Stratex Real State Files ===")
for jf in ['engine-health.json', 'binance_state.json', 'active_trades.json', 'advisory_quality_report.json', 'deployment_audit_log.json']:
    p = os.path.join(base, 'Stratex', jf)
    if os.path.exists(p):
        with open(p, 'r', encoding='utf-8', errors='ignore') as f:
            try:
                data = json.load(f)
                print(f"  File: {jf} | Size: {os.path.getsize(p)} bytes")
                print(f"    Preview: {str(data)[:250]}...")
            except Exception as e:
                print(f"  Error reading {jf}: {e}")
