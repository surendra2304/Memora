# 🏛️ SYSTEM MANIFEST — Memora Persistent Memory Fabric

> **Official Subsystem Name:** Memora  
> **Role in Ecosystem:** Unified Long-Term Memory Fabric & RBAC Partitioned Vector/Episodic Vault  
> **Repository:** [surendra2304/Memora](https://github.com/surendra2304/Memora) (Branch: master)  
> **Workspace Path:** d:\FRIDAY Universe\Memora  

---

## ☁️ 1. Configured service (runtime unverified)

| Attribute | Repository configuration |
| :--- | :--- |
| **Configured Service URL (deployment unverified)** | [https://memora-cavc.onrender.com](https://memora-cavc.onrender.com) |
| **Health Check Endpoint** | https://memora-cavc.onrender.com/health |
| **API key variable (keep value in secret environment)** | `MEMORA_API_KEY` (unique secret configured outside source control) |
| **Authentication Header** | `Authorization: Bearer <MEMORA_API_KEY>` |
| **Configured database topology (runtime unverified)** | Turso LibSQL Cloud DB (configured; current capacity/plan must be verified in Turso) |
| **Configured database URL or namespace (not a secret)** | libsql://memora-db-surendra2304.aws-ap-south-1.turso.io |
| **Configured host (plan, region, and runtime unverified)** | Render service configured (current plan, region, and deployment unverified) |

---

## 🎯 2. Purpose & Responsibilities

### What Memora IS:
* Memora is the persistent memory fabric of the ecosystem. It provides URI-partitioned memory namespaces (memora://<agent>/private) and RBAC security. Live database capacity and availability require verification in Turso.

### What Memora DOES:
* Operates as the **Unified Long-Term Memory Fabric & RBAC Partitioned Vector/Episodic Vault** within the 9-agent FRIDAY Universe.
* Communicates directly with peer agents via authenticated REST and WebSocket protocols.
* Persists private long-term memory records to **Memora** under memora://memora/private.

---

## 🌐 3. Ecosystem endpoint configuration

These variable names and URLs are references only; they do not prove live communication. Set real credentials in secret environments.

```env
# ============================================================================== #
#               FRIDAY UNIVERSE MASTER ECOSYSTEM CONFIGURATION                  #
# ============================================================================== #

# 1. ⚡ Inference AI Multi-Model Gateway
INFERENCE_URL=https://inference-r1sn.onrender.com
# Configure a unique Inference service key outside source control.

# 2. Memora cloud memory service (active backend/capacity not verified)
MEMORA_URL=https://memora-cavc.onrender.com
# Configure a unique Memora service key outside source control.

# 3. 📈 Stratex Paper/Testnet Strategy Platform (Binance Futures)
STRATEX_URL=https://stratex-8wj1.onrender.com
# Configure a unique Stratex service key outside source control.

# 4. IntelX research service (active storage backend not verified)
INTELX_URL=https://intelx-mygl.onrender.com
# Configure a unique IntelX service key outside source control.

# 5. 🔮 Futuris Calibrated Predictive Forecasting Engine
FUTURIS_URL=https://futuris-th6f.onrender.com
# Configure a unique Futuris service key outside source control.

# 6. 🌐 Cortex Autonomous Web Operations & Intelligence
CORTEX_URL=https://cortex-0m7c.onrender.com
# Configure a unique Cortex service key outside source control.

# 7. 🛠️ Forge Local Software Engineering Engine
FORGE_URL=https://forge-e9kl.onrender.com
# Configure a unique Forge service key outside source control.

# 8. 🛡️ Sentinel Local Cybersecurity & Threat Defense Shield
SENTINEL_URL=https://sentinel-a861.onrender.com
# Configure a unique Sentinel service key outside source control.

# 9. 🤖 FRIDAY Central Desktop Operating System
FRIDAY_URL=https://friday-zw59.onrender.com
# Configure a unique FRIDAY service key outside source control.
```

---

## 🤖 4. Repository guide

When opening this repository:
* **Identity:** You are working inside **Memora** (d:\FRIDAY Universe\Memora).
* **Configured URL (deployment unverified): https://memora-cavc.onrender.com.
* **Authentication:** Incoming requests require a unique `MEMORA_API_KEY` configured in the service environment.
* **Test Evidence:** Label local tests, test doubles, and live endpoint checks separately; report only results actually observed.
* **No Unapproved Git Pushes:** Keep modifications local unless explicitly instructed to push.
