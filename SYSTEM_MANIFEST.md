# 🏛️ SYSTEM MANIFEST — Memora Persistent Memory Fabric

> **Official Subsystem Name:** Memora  
> **Role in Ecosystem:** Unified Long-Term Memory Fabric & RBAC Partitioned Vector/Episodic Vault  
> **Repository:** [surendra2304/Memora](https://github.com/surendra2304/Memora) (Branch: master)  
> **Workspace Path:** d:\FRIDAY Universe\Memora  

---

## ☁️ 1. Live Cloud Infrastructure & Deployment

| Attribute | Production Configuration |
| :--- | :--- |
| **Live Production URL** | [https://memora-9zr9.onrender.com](https://memora-9zr9.onrender.com) |
| **Health Check Endpoint** | https://memora-9zr9.onrender.com/health |
| **Master API Key Variable** | MEMORA_API_KEY=memora_api |
| **Authentication Header** | Authorization: Bearer memora_api / X-API-KEY: memora_api |
| **Database Topology** | Turso LibSQL Cloud DB (9 GB Free Tier) |
| **Database Connection** | libsql://memora-db-surendra2304.aws-ap-south-1.turso.io |
| **Hosting Platform** | Render Docker Web Service (Singapore / AWS Mumbai) |

---

## 🎯 2. Purpose & Responsibilities

### What Memora IS:
* Memora is the universal persistent memory fabric of the ecosystem. Backed by a 9 GB Turso cloud database in AWS Mumbai, it provides URI-partitioned memory namespaces (memora://<agent>/private) and RBAC security for all agents.

### What Memora DOES:
* Operates as the **Unified Long-Term Memory Fabric & RBAC Partitioned Vector/Episodic Vault** within the 9-agent FRIDAY Universe.
* Communicates directly with peer agents via authenticated REST and WebSocket protocols.
* Persists private long-term memory records to **Memora** under memora://memora/private.

---

## 🌐 3. Full Ecosystem Network Connectivity

Every agent in the universe communicates using standard environment variables:

`env
# ============================================================================== #
#               FRIDAY UNIVERSE MASTER ECOSYSTEM CONFIGURATION                  #
# ============================================================================== #

# 1. ⚡ Inference AI Multi-Model Gateway (25 Keys)
INFERENCE_URL=https://inference-3i2b.onrender.com
INFERENCE_API_KEY=inference_api

# 2. 🧠 Memora Cloud Persistent Memory (9 GB Turso AWS Mumbai)
MEMORA_URL=https://memora-9zr9.onrender.com
MEMORA_API_KEY=memora_api

# 3. 📈 Stratex 24/7 Algorithmic Trading Platform (Binance Futures)
STRATEX_URL=https://stratex-ucjz.onrender.com
STRATEX_API_KEY=stratex_api

# 4. 🧠 IntelX Evidence & Intelligence Research Engine (Turso AWS Mumbai)
INTELX_URL=https://intelx-3cz1.onrender.com
INTELX_API_KEY=intelx_api

# 5. 🔮 Futuris Calibrated Predictive Forecasting Engine
FUTURIS_URL=https://futuris-x4f4.onrender.com
FUTURIS_API_KEY=futuris_api

# 6. 🌐 Cortex Autonomous Web Operations & Intelligence
CORTEX_URL=https://cortex-qifr.onrender.com
CORTEX_API_KEY=cortex_api

# 7. 🛠️ Forge Local Software Engineering Engine
FORGE_URL=http://localhost:8001
FORGE_API_KEY=forge_api

# 8. 🛡️ Sentinel Local Cybersecurity & Threat Defense Shield
SENTINEL_URL=http://localhost:8003
SENTINEL_API_KEY=sentinel_api

# 9. 🤖 FRIDAY Central Desktop Operating System
FRIDAY_URL=http://localhost:9000
FRIDAY_API_KEY=friday_api
`

---

## 🤖 4. Antigravity AI Session Guide

When opening this directory in **Antigravity AI**:
* **Identity:** You are working inside **Memora** (d:\FRIDAY Universe\Memora).
* **Live Service:** This service is deployed live at https://memora-9zr9.onrender.com.
* **Authentication:** Incoming requests use MEMORA_API_KEY=memora_api.
* **Never Fake Tests:** All tests and verifications must be executed against real code and real endpoints.
* **No Unapproved Git Pushes:** Keep modifications local unless explicitly instructed to push.
