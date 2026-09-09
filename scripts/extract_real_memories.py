"""
Extract real memory records directly from the 9 agent databases and systems
"""
import os
import sqlite3
import json

base = 'd:/FRIDAY Universe'

print("=== FRIDAY REAL MESSAGES / SESSIONS ===")
conn = sqlite3.connect(os.path.join(base, 'FRIDAY/data/friday.db'))
c = conn.cursor()
c.execute("SELECT m.role, m.content, m.created_at FROM messages m JOIN conversations cv ON m.conversation_id = cv.id WHERE length(m.content) > 20 LIMIT 10")
for r in c.fetchall():
    print(f"[{r[0]}] ({r[2]}): {r[1][:140]}...")
conn.close()

print("\n=== FORGE REAL TASKS & AUDIT ===")
conn = sqlite3.connect(os.path.join(base, 'Forge/data/forge.db'))
c = conn.cursor()
c.execute("SELECT goal, requirements, state, created_at, metadata FROM tasks LIMIT 5")
for r in c.fetchall():
    print(f"Goal: {r[0]} | Req: {r[1]} | State: {r[2]} | At: {r[3]}")
c.execute("SELECT event_type, payload, timestamp FROM audit_events LIMIT 5")
for r in c.fetchall():
    print(f"Audit: {r[0]} | Payload: {r[1][:100]} | At: {r[2]}")
conn.close()

print("\n=== INFERENCE REAL MEMORIES & TASKS ===")
conn = sqlite3.connect(os.path.join(base, 'Inference/data/universe.db'))
c = conn.cursor()
c.execute("SELECT agent_id, content, memory_type, importance, tags, created_at FROM memories LIMIT 8")
for r in c.fetchall():
    print(f"Agent: {r[0]} | Type: {r[2]} | Imp: {r[3]} | Content: {r[1][:120]}... | Tags: {r[4]}")
conn.close()

print("\n=== INTELX REAL CLAIMS ===")
conn = sqlite3.connect(os.path.join(base, 'IntelX/data/intelx.db'))
c = conn.cursor()
c.execute("SELECT id, text, quote FROM claims_fts LIMIT 8")
for r in c.fetchall():
    print(f"ID: {r[0]} | Text: {r[1]}")
conn.close()

print("\n=== FUTURIS REAL FORECASTS ===")
conn = sqlite3.connect(os.path.join(base, 'Futuris/data/futuris.db'))
c = conn.cursor()
c.execute("SELECT target, as_of, horizon, probability, confidence, status, drivers FROM forecasts LIMIT 8")
for r in c.fetchall():
    print(f"Target: {r[0]} | Prob: {r[3]} | Conf: {r[4]} | Status: {r[5]} | Drivers: {str(r[6])[:80]}")
conn.close()
