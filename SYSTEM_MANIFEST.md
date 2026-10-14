# 🏛️ SYSTEM MANIFEST — Memora Persistent Memory Fabric

> **Official Subsystem Name:** Memora  
> **Role in Ecosystem:** Unified Long-Term Memory Fabric & RBAC Partitioned Vector/Episodic Vault  
> **Repository:** [surendra2304/Memora](https://github.com/surendra2304/Memora) (Branch: main)  
> **Workspace Path:** d:\FRIDAY Universe\Memora  

---

## ☁️ 1. Modernized Infrastructure (Oracle Cloud Always Free)

| Attribute | Repository configuration |
| :--- | :--- |
| **Public Gateway URL** | [https://friday-universe.duckdns.org/memora](https://friday-universe.duckdns.org/memora) |
| **Health Check Endpoint** | https://friday-universe.duckdns.org/memora/health |
| **Internal Mesh Service URL** | http://memora:8000 |
| **Internal Container Port** | 8000 (Host Port: 8006) |
| **API key variable** | `MEMORA_API_KEY` (configured via environment) |
| **Authentication Header** | `Authorization: Bearer <MEMORA_API_KEY>` |
| **Database Engine** | SQLite 3 with WAL Mode (`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;`) + Alembic migrations |
| **Container Network** | `friday_mesh` |

---

## 🎯 2. Purpose & Responsibilities

### What Memora IS:
* Memora is the persistent episodic memory, knowledge graph, and vector recall engine for all 8 agents in the FRIDAY Universe. It provides URI-partitioned memory namespaces (`memora://<agent>/private`) and RBAC security.

### What Memora DOES:
* Operates as the **Unified Long-Term Memory Fabric & RBAC Partitioned Vector/Episodic Vault** within the FRIDAY Universe.
* Communicates directly with peer agents via authenticated REST and WebSocket protocols within `friday_mesh`.
* Persists private long-term memory records to **Memora** under `memora://memora/private`.

---

## 🌐 3. Ecosystem Endpoint Configuration (`friday_mesh`)

```env
# ============================================================================== #
#               FRIDAY UNIVERSE MASTER ECOSYSTEM CONFIGURATION                  #
# ============================================================================== #

# 1. ⚡ Inference AI Multi-Model Gateway
INFERENCE_URL=http://inference:8000

# 2. 🧠 Memora Memory & Knowledge Graph Service
MEMORA_URL=http://memora:8000

# 3. 📈 Stratex Paper/Testnet Strategy Platform
STRATEX_URL=http://stratex:8000

# 4. 🔍 IntelX Autonomous Research & OSINT Engine
INTELX_URL=http://intelx:8000

# 5. 🔮 Futuris Calibrated Predictive Forecasting Engine
FUTURIS_URL=http://futuris:8000

# 6. 🌐 Cortex Autonomous Web Operations & Intelligence
CORTEX_URL=http://cortex:8000

# 7. 🛠️ Forge Local Software Engineering Engine
FORGE_URL=http://forge:8000

# 8. 🛡️ Sentinel Cybersecurity & Threat Defense Shield
SENTINEL_URL=http://sentinel:8000

# 9. 🤖 FRIDAY Central Gateway / Orchestrator
FRIDAY_URL=http://friday:10000
```

---

## 🤖 4. Repository Guide

When working in this repository:
* **Identity:** You are working inside **Memora** (`d:\FRIDAY Universe\Memora`).
* **Container Port:** 8000 (Host Port: 8006).
* **Mesh URL:** `http://memora:8000`.
* **Public Gateway:** `https://friday-universe.duckdns.org/memora`.
* **Authentication:** Incoming requests require a configured `MEMORA_API_KEY`.
* **Database:** SQLite 3 with WAL mode and Alembic migrations.
