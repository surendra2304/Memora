# 🧠 Memora — Multi-Tier Cognitive Memory Engine

[![CI / Diary Verification](https://github.com/surendra2304/Memora/actions/workflows/verify.yml/badge.svg)](https://github.com/surendra2304/Memora)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Memora** is an autonomous long-term cognitive memory fabric designed for AI agent ecosystems (such as the FRIDAY Universe). It provides multi-tier persistent memory, hybrid retrieval (FTS5 keyword + dense vector embeddings with Reciprocal Rank Fusion), episodic event graphing, and automated knowledge consolidation without heavy vendor dependencies.

---

## 🌟 Key Architecture & Capabilities

1. **Multi-Tier Memory Fabric**:
   - **Episodic Memory**: Temporal event sequences, conversation logs, and causal action chains.
   - **Semantic Memory**: Persistent entities, relational facts, and domain knowledge graphs.
   - **Procedural Memory**: Reusable tool execution patterns, skills, and validated workflows.
   - **Working Memory**: Low-latency conversational context buffer for active task reasoning.

2. **Reciprocal Rank Fusion (RRF) Hybrid Search**:
   - SQLite 3 FTS5 lexical keyword matching for high-precision exact queries.
   - Pluggable dense vector embedding matching for semantic similarity.
   - Calibrated RRF scoring merging lexical and semantic result sets.

3. **Nightly Consolidation & Decay**:
   - Autonomous background job pruning transient episodic noise.
   - High-confidence knowledge promotion into permanent semantic store.

4. **Hardened Security Gates**:
   - Strict capability gating: `memory_control` > `knowledge_indexing` > `retrieval_access`.
   - Complete ACID compliance with zero uncommitted state leakages.

---

## 📖 Engineering Diary & Progress Tracking

Memora follows a strict, day-wise engineering diary protocol to track every architectural decision, implementation milestone, bug fix, and test metric:

- **Executive Summary & Master Index**: [MEMORA_DIARY.md](MEMORA_DIARY.md)
- **Daily Logs**: Located in the [`diary/`](diary/) directory (e.g., [Day 1: 2026-08-29](diary/2026-08-29.md))

---

## 🚀 Quick Verification

Run automated validation of diary line constraints and memory safety invariants:

```bash
python scripts/verify_diary.py
```
