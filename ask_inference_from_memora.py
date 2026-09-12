import asyncio
import os
import sys
import time
import httpx
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
load_dotenv()

questions = [
    "What vector dimensionality and chunking strategy maximizes episodic memory retrieval precision?",
    "How can memory consolidation mitigate context window overflow across multi-day operational sessions?",
    "What is the optimal temporal decay weighting policy for working memory vs long-term storage?",
    "Evaluate indexing trade-offs between HNSW and IVFFlat for cloud Turso SQLite vector databases.",
    "How should cross-agent memory namespaces be partitioned to prevent data leakage between subsystems?"
]

async def main():
    print("=" * 80)
    print("AGENT [7/9]: MEMORA -> INFERENCE GATEWAY (5 QUESTIONS)")
    print("Client: Memora Ecosystem Integration Client")
    print("=" * 80)
    
    inf_url = os.getenv("INFERENCE_URL", "https://inference-3i2b.onrender.com").rstrip("/")
    api_key = os.getenv("INFERENCE_API_KEY", "inference_api")
    print(f"Target URL: {inf_url}")
    print(f"API Key:    {api_key[:4]}...")
    
    headers = {
        "X-FRIDAY-API-Key": api_key,
        "X-Originating-Service": "memora",
        "Content-Type": "application/json"
    }
    
    results = []
    async with httpx.AsyncClient(timeout=90.0) as client:
        for i, q in enumerate(questions, 1):
            t0 = time.perf_counter()
            payload = {
                "question": q,
                "caller_id": "memora_vector_fabric",
                "context_data": {"agent": "memora", "domain": "persistent_memory"}
            }
            try:
                resp = await client.post(f"{inf_url}/v1/friday/ask", json=payload, headers=headers)
                lat = (time.perf_counter() - t0) * 1000
                if resp.status_code == 200:
                    data = resp.json()
                    run_id = data.get("run_id", "N/A")
                    ans_snip = data.get("answer", "")[:120].replace("\n", " ")
                    print(f"[MEMORA Q{i}/5] HTTP 200 | {lat:>7.1f}ms | Run: {run_id} | Ans: {ans_snip}...")
                    results.append({"q_num": i, "status": 200, "latency_ms": round(lat, 1), "run_id": run_id, "answer": ans_snip})
                else:
                    print(f"[MEMORA Q{i}/5] HTTP {resp.status_code} | {lat:>7.1f}ms")
                    results.append({"q_num": i, "status": resp.status_code, "latency_ms": round(lat, 1)})
            except Exception as e:
                lat = (time.perf_counter() - t0) * 1000
                print(f"[MEMORA Q{i}/5] ERROR | {lat:>7.1f}ms | {e}")
                results.append({"q_num": i, "status": "ERROR", "latency_ms": round(lat, 1), "error": str(e)})
                
    print("-" * 80)
    lats = [r["latency_ms"] for r in results if r["status"] == 200]
    if lats:
        print(f"MEMORA Batch Complete: Avg Latency = {sum(lats)/len(lats):.1f}ms (Min: {min(lats):.1f}ms, Max: {max(lats):.1f}ms)")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
