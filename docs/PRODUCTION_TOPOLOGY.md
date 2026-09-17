# AURA — Production Topology, Deployment Architecture & Operational Runbook

**Classification:** System Architecture & Production Operations  
**Status:** IMPLEMENTED & PRODUCTION-HARDENED  
**Version:** 2.0.0  

---

## 1. Production Topology Overview

AURA implements a three-tier distributed topology designed for high availability, privacy, and continuous offline-first resilience:

```text
                    ┌──────────────────────────────┐
                    │         Render Cloud         │
                    │                              │
                    │  API Gateway & Public Relay  │
                    │  Sync Event Dispatcher       │
                    │  Device Gateway / Polling    │
                    └──────────────┬───────────────┘
                                   │
                        HTTPS      │      HTTPS
                    ┌──────────────┘ └──────────────┐
                    ↓                               ↓
       ┌─────────────────────────┐     ┌─────────────────────────┐
       │      AURA Laptop        │     │     Android Device      │
       │                         │     │                         │
       │  Primary Brain Runtime  │     │  Device Accessibility   │
       │  Local Models (GGUF)    │     │  Local UI Perception    │
       │  Continuous Learning    │     │  Offline Queue / Ledger │
       │  Episodic & Semantic DB │     │  Companion Native UI    │
       └─────────────────────────┘     └─────────────────────────┘
```

---

## 2. Component Responsibility Matrix

| Subsystem | Render Cloud Node | Laptop Workstation Node | Android Companion Node |
| :--- | :--- | :--- | :--- |
| **Primary Brain / LLM** | Cloud Gateway / Router (Gemini / Claude / Cloud fallback) | **Authoritative Local Brain** (GGUF / llama.cpp / RTX 4060 GPU) | Client-side streaming UI display only |
| **Continuous Learning** | Inert (no heavy training) | **Full Training Pipeline** (LoRA training, candidate eval, canary) | Experience generator (records mobile interactions) |
| **Storage & Memory** | Persistent volume at `/app/data` (relayed events, sync inbox/outbox) | **Authoritative Master Memory** (`data/memory.db`: episodic, semantic, profile) | Local cache & SQLite/SharedPreferences |
| **Tool Execution** | Gateway relay & coordinator | Desktop tools (`system.*`, `desktop.*`, `filesystem.*`) | Android tools (`android.*`: accessibility, tap, launch) |
| **Network Presence** | Public internet (HTTPS / TLS) | Private LAN / Wi-Fi | Cellular (4G/5G) / Wi-Fi |

---

## 3. Offline Behavior & Degraded Modes

AURA does **not** fail if any single node becomes disconnected. It transitions deterministically into documented degraded modes:

### Scenario A: Render Cloud Unavailable / Internet Down
1. **Laptop Behavior:**
   - Operates 100% locally with zero degradation in reasoning, memory lookup, or desktop automation.
   - Outgoing sync events queue in `sync_outbox` with status `PENDING`.
   - Exponential backoff with jitter prevents socket hammering.
2. **Android Behavior:**
   - Retains local UI cache and queuing capability.
   - Background Accessibility Service buffers local gestures and interactions in `FileInvocationLedger`.
3. **Reconnection & Convergence:**
   - Once Render comes back online, both Laptop and Android flush their outbox queues monotonically in batches of 50.
   - Monotonic logical sequence numbers and SHA-256 payload hashes prevent duplicate application.

### Scenario B: Android Disconnected / Phone Asleep
1. **Laptop Behavior:**
   - Tool calls requiring `android.*` are hedged or reported honestly as `DEVICE_UNAVAILABLE`.
   - Desktop and cloud tasks continue unimpeded.
2. **Device Queue:**
   - Pending commands remain queued until the device initiates its next polling cycle (`POST /api/device/poll`).

---

## 4. Disaster Recovery & Backup Architecture

### 4.1 Automated Online SQLite Backup
AURA includes a built-in, zero-downtime online backup system ([`memory/backup.py`](file:///D:/AURA/memory/backup.py)):
- Uses Python's native `sqlite3.Connection.backup()` under the process-wide `db_lock`.
- Safely copies pages while read and write queries continue executing.
- Verifies every created snapshot via `PRAGMA integrity_check`.
- Automatically prunes older backups beyond a configurable threshold (`max_backups_to_keep`, default 5) to guarantee bounded disk usage.

### 4.2 Restoring from Backup
```bash
# Programmatic restore
python -c "from memory.backup import restore_database_backup; restore_database_backup('data/backups/memory_backup_latest.db')"
```
- Restoring creates an automatic safety snapshot (`memory.db.pre_restore.bak`) before overwriting.

---

## 5. Security & Observability Armor

1. **Secret Masking Filter:**
   - All logs passing through `core.logger` are automatically scrubbed of Bearer tokens, API keys, and authorization headers via `SecretMaskingFilter`.
2. **Zero Leaked Secrets In Repo:**
   - `.env` and cryptographic key files are strictly untracked in Git (`.gitignore`).
3. **Structured REST Errors:**
   - API endpoints output structured JSON errors with tracking IDs and actionable machine error codes (`HASH_MISMATCH`, `AMBIGUOUS_CRASH_RECOVERY`, `BLOCKED_PERMISSION`).

---

## 6. Autonomy Guard (State 3 Lock)

- **Hard Security Constraint:** Full unconstrained autonomous self-learning (`full_autonomy_enabled`) is **STRICTLY LOCKED** (`False`).
- **Operational Rule:** The system is certified in **STATE 2** (Canary-Only Autonomous Self-Learning with Mandatory Evaluation Gating). Any model candidate generated by the background learning scheduler requires explicit human supervisor sign-off before production promotion.
