"""
Deep inspect real databases across FRIDAY, Forge, Sentinel, Inference, Cortex
"""
import os
import sqlite3
import json

base = 'd:/FRIDAY Universe'

def show_db_details(db_path, title):
    if not os.path.exists(db_path):
        print(f"[-] {title} not found at {db_path}")
        return
    print(f"\n*** {title} ***")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [r[0] for r in c.fetchall()]
    for t in tables:
        try:
            c.execute(f"SELECT count(*) FROM \"{t}\"")
            cnt = c.fetchone()[0]
            if cnt > 0:
                print(f"  {t} ({cnt} rows):")
                c.execute(f"SELECT * FROM \"{t}\" LIMIT 2")
                cols = [d[0] for d in c.description]
                print(f"    cols: {cols}")
                for row in c.fetchall():
                    print("    data:", str(row)[:140])
        except Exception as e:
            print(f"  {t}: {e}")
    conn.close()

show_db_details(os.path.join(base, 'FRIDAY/data/friday.db'), 'FRIDAY friday.db')
show_db_details(os.path.join(base, 'FRIDAY/src/data/friday.db'), 'FRIDAY src/data/friday.db')
show_db_details(os.path.join(base, 'Forge/data/forge.db'), 'Forge forge.db')
show_db_details(os.path.join(base, 'Cortex/data/cortex.db'), 'Cortex cortex.db')
show_db_details(os.path.join(base, 'Inference/data/universe.db'), 'Inference universe.db')
show_db_details(os.path.join(base, 'Sentinel/data/live_test.db'), 'Sentinel live_test.db')
