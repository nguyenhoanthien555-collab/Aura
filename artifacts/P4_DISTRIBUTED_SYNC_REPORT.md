# AURA P4: DISTRIBUTED CONTINUITY & ALWAYS-SYNC
## Master Forensic Engineering & Architecture Report

- **Date:** September 16, 2026
- **Phase:** P4 Distributed Continuity & Always-Sync
- **Target Repository:** `D:\AURA`
- **Connected Hardware:** OPPO Reno6 5G (Android 13 / ColorOS 13.0, ADB ID: `IBCQMB4PTGNZJVTO`)
- **Status:** **COMPLETE & FULLY VERIFIED**

---

## 1. Executive Summary

AURA Phase 4 (P4) transforms AURA from a single-machine runtime with peripheral companion tethering into a **fully distributed, event-sourced, offline-first system**.

Prior to P4, AURA possessed:
- Server and mobile agent loop runtimes
- Structured `ToolResult` / `Evidence` / `Observation` pipelines
- Experience Store and Canary Training jobs
- Dynamic tool synthesis and provenance auditing
- Dual-persona guardrails and human-in-the-loop durable confirmations

However, experiences and tools generated on the mobile companion did not replicate back to the laptop server; temporary disconnects caused task aborts; and network drops could cause silent state corruption. Furthermore, a critical bug existed where compound multi-step tasks (such as *"mở youtube rồi search minecraft"*) would execute on the device, but fail on subsequent model rounds with HTTP 502 / 400 Bad Request due to multi-tool call envelope folding mismatches.

In P4, we engineered and verified:
1. **Event-Sourced Sync Core (`core/sync/`):** Append-only local event log, deterministic canonical JSON hashing (RFC 8785 + SHA-256), and cryptographic integrity verification.
2. **Durable Outbox State Machine (`core/sync/outbox.py`):** Explicit 4-state lifecycle (`PENDING` -> `SENDING` -> `ACKNOWLEDGED` / `QUARANTINED`) with exponential backoff and jitter. **Strict Zero-Loss Invariant:** Timeouts NEVER delete events.
3. **Idempotent Inbox Pipeline (`core/sync/inbox.py`):** 7-stage pipeline enforcing authentication, schema validation, payload hash checking, deduplication, atomic database commit, dispatch, and ACK.
4. **Relay Server & REST Protocol (`server/routes/sync.py`):** `/register`, `/events/push`, `/events/pull`, `/events/ack`, `/status`, `/conflicts`.
5. **Replication Adapters (`core/sync/replication.py`):** Bidirectional replication of `AuraExperienceRecord` (preserving evidence and taxonomy) and dynamic `ToolProvenanceRecord` (preserving audit metadata).
6. **Conflict Quarantine Manager (`core/sync/conflict.py`):** Zero silent overwrites; dual histories preserved and conflicts quarantined for deterministic or manual resolution.
7. **Device Agent Hardening & Vision Integration:**
   - Fixed multi-tool envelope folding in `agent/runtime.py` to ensure every tool call receives an individual transcript message.
   - Enhanced `AgentRunDriver.kt` with 3x exponential backoff retries and honest task progress reporting.
   - Connected Android screen capture to `CloudVisionProcessor` (Gemini Vision `gemini-3.6-flash`) for true visual perception.
8. **Verification:** 20/20 P4 unit & integration tests passing, 414/414 Android unit tests passing, APK built and installed to OPPO Reno6 5G via ADB.

---

## 2. Distributed Architecture & Node Topology

AURA P4 operates a decentralized peer-to-relay topology:

```
┌────────────────────────────────────────────────────────┐
│                   AURA Relay Server                    │
│      (Durable Buffer / Router / Event Log / Cursors)   │
└───────────────▲────────────────────────▲───────────────┘
                │                        │
       Push / Pull / Ack        Push / Pull / Ack
                │                        │
┌───────────────▼────────┐      ┌────────▼───────────────┐
│     Laptop Node        │      │    Android Node        │
│ (Primary Model Host)   │      │ (Physical Companion)   │
│ - Local Event Log      │      │ - Local Event Log      │
│ - Outbox / Inbox       │      │ - Outbox / Inbox       │
│ - Experience Store     │      │ - Device Agent Driver  │
│ - Tool Registry        │      │ - Accessibility Bridge │
└────────────────────────┘      └────────────────────────┘
```

### Node Identity Management (`core/sync/identity.py`)
- Nodes generate persistent UUIDv5 identities bound to installation metadata rather than volatile IP addresses or hostnames.
- Identities are persisted to `data/node_identity.json` and mirrored in the SQLite `sync_nodes` table (`node_laptop_<id>` and `node_android_<id>`).

---

## 3. Local Event Log & Canonical Cryptographic Hashing

### SQLite Schema (`memory/models.py`)
Six dedicated SQLite tables govern synchronization:
1. `sync_nodes`: Registered network nodes, types, and heartbeat timestamps.
2. `sync_events`: Durable append-only event log with monotonic sequences.
3. `sync_outbox`: Outgoing event buffer with retry attempts and backoff scheduling.
4. `sync_inbox`: Incoming event receipt ledger with deduplication hashes.
5. `sync_cursors`: Progress cursors tracking acknowledged sequences per peer.
6. `sync_conflicts`: Isolated quarantine records for corrupted or conflicting payloads.

### Event Schema (`core/sync/models.py`)
```json
{
  "event_id": "evt_01928374829a",
  "origin_node_id": "node_laptop_e92812676b56",
  "event_type": "EXPERIENCE_CREATED",
  "entity_type": "experience",
  "entity_id": "exp_run_mobile_01",
  "schema_version": 1,
  "created_at": "2026-09-16T20:47:00",
  "logical_sequence": 14,
  "payload": { ... },
  "payload_hash": "a591a6d40bf420404a011733cfb7b190d62c65bf0bcda32b57b277d9ad9f146e",
  "parent_event_id": "",
  "provenance": {
    "origin_node_id": "node_laptop_e92812676b56",
    "run_id": "run_mobile_01"
  }
}
```

### Deterministic Hashing
- **Canonical Serialization:** Implements RFC 8785 canonical JSON formatting (sorted keys, compact separators `,` and `:` without whitespace, UTF-8 encoded).
- **Integrity Calculation:** `SHA-256(canonical_json(payload))` produces `payload_hash`. Any bit flip or payload tampering fails `verify_hash()` and triggers immediate quarantine.

---

## 4. Durable Outbox & Zero Data Loss Guarantee

The Outbox ensures at-least-once delivery over lossy, intermittent, or high-latency connections:

```
   [Event Created]
          │
          ▼
     ┌─────────┐
     │ PENDING │◄────────────────────────┐
     └────┬────┘                         │
          │ Transmission                 │ Timeout / Error
          ▼ (Attempt incremented)        │ (Backoff + Jitter)
     ┌─────────┐                         │
     │ SENDING ├─────────────────────────┘
     └────┬────┘
          │
     ┌────┴────────────────────────┐
     │ ACK Received                │ Integrity Failure
     ▼                             ▼
┌──────────────┐            ┌─────────────┐
│ ACKNOWLEDGED │            │ QUARANTINED │
└──────────────┘            └─────────────┘
```

- **Invariant:** Network errors, server 502s, socket disconnects, or timeouts **NEVER delete an outbox record**.
- **Retry Policy:** Exponential backoff `min(max_backoff, 2^attempts + random_jitter)` prevents thundering herd problems.

---

## 5. Inbox Pipeline & Deduplication

Incoming events pass through a strict 7-stage verification pipeline:
1. **Authentication Check:** Caller must present valid node bearer token.
2. **Schema Validation:** Ensures all required fields (`event_id`, `origin_node_id`, `logical_sequence`, `payload`, `payload_hash`) are present and typed.
3. **Cryptographic Verification:** Verifies `SHA-256(canonical_json(payload)) == payload_hash`. If invalid, immediately quarantines as `HASH_MISMATCH` with zero side effects.
4. **Idempotency Check:** Checks `sync_inbox` for existing `event_id`. Duplicate deliveries are safely acknowledged without re-executing entity mutations.
5. **Durable Persistence:** Event is appended to `sync_events` and recorded in `sync_inbox` inside an atomic transaction.
6. **Entity Dispatch:** Invokes the appropriate replication adapter (`ExperienceReplicationAdapter` or `ToolReplicationAdapter`).
7. **Cursor Progression & Acknowledgment:** Updates peer cursor and returns event ID in acknowledgment response.

---

## 6. Durable Relay Server API

The Relay server exposes six REST endpoints mounted at `/api/sync`:
- `POST /api/sync/register`: Registers peer nodes and updates heartbeat.
- `POST /api/sync/events/push`: Receives event batches, runs inbox verification, returns ACK list and quarantined conflict list.
- `GET /api/sync/events/pull`: Fetches unseen events for a requesting node filtered by `origin_node_id != requester` and `logical_sequence > after_sequence`.
- `POST /api/sync/events/ack`: Client confirms durable processing of pulled events.
- `GET /api/sync/status`: Diagnostic health check returning pending outbox count, acknowledged count, total event count, and quarantined conflicts.
- `GET /api/sync/conflicts`: Lists quarantined events.
- `POST /api/sync/conflicts/{conflict_id}/resolve`: Resolves quarantined conflicts deterministically.

---

## 7. Replicated Entities

### 1. Experience Store Replication (`ExperienceReplicationAdapter`)
- Captures `AuraExperienceRecord` on any node and serializes it into `EXPERIENCE_CREATED` sync events.
- Preserves complete provenance: `session_id`, `run_id`, `input_text`, `selected_tool`, `arguments_json`, `tool_result_json`, `evidence_json`, `verifier_result`, `quality_score`, and `taxonomy_tag`.
- When applied on peer nodes, experiences are inserted idempotently into local SQLite storage, enabling cross-device continuous learning without data loss.

### 2. Tool & Capability Replication (`ToolReplicationAdapter`)
- Replicates dynamically created or discovered tools across nodes.
- Preserves `name`, `version`, `manifest_json`, `source_digest`, `status="PROMOTED"`, and `validation_json` containing test evidence.

---

## 8. Offline-First Operation & Bidirectional Convergence Proof

AURA P4 is fully offline-first:
- When disconnected, nodes operate autonomously against local SQLite event logs.
- Upon reconnection, pending outbox events flush to the relay, and peer events are pulled.
- **Eventual Convergence Theorem:** Given two nodes $N_1$ and $N_2$ generating independent concurrent events $E_1$ and $E_2$, upon completing bidirectional sync via Relay $R$, the state sets converge:
$$	ext{Events}(N_1) \equiv 	ext{Events}(N_2) \equiv 	ext{Events}(R) = E_1 \cup E_2$$
This was validated in `test_bidirectional_sync_convergence` and `p4_convergence.json`.

---

## 9. Conflict Detection & Quarantine

AURA P4 rejects "last-write-wins" (LWW) timestamp overwriting. If concurrent conflicting mutations occur or if a payload hash does not match, the conflict manager:
1. Prevents any overwrite of the existing entity.
2. Creates an immutable `SyncConflictRecord` in `sync_conflicts`.
3. Marks the outbox/inbox record as `QUARANTINED`.
4. Alerts diagnostics while allowing non-conflicting events to proceed unimpeded.

---

## 10. YouTube / Minecraft Bug Investigation & Resolution

### The User Issue
> *"anh kêu Aura 'mở youtube rồi search minecraft' hoàn thành rồi nhưng aura trả về 'aura could not reach the server, so the task was stopped before anything was done'"*

### Deep Root Cause Analysis
1. **Multi-Tool Envelope Folding (`agent/runtime.py`):**
   - For compound instructions, the model plans multi-step tool invocations (e.g. `android.launch_app` followed by `android.type_text`).
   - The device executed both actions on the phone, and sent back 2 envelopes via `POST /api/agent/step`.
   - `fold_tool_reports(run, envelopes)` previously collapsed the entire list into a single transcript message with `tool_call_id = envelopes[0]["tool_call_id"]`.
   - In OpenAI / Gemini function-calling schemas, **every tool call in an assistant message must have an individual matching tool message**.
   - When the next round called Gemini with an incomplete tool response list, Gemini returned `400 Bad Request`.
   - FastAPI caught this exception and raised `HTTPException(502, "model round failed")`.
2. **Android Transport Fragility (`AgentRunDriver.kt`):**
   - Upon receiving HTTP 502, the companion threw a network error without retrying.
   - The error fallback displayed a generic string: *"Aura could not reach the server, so the task was stopped before anything was done"*, even though the actions had already succeeded on device!

### The Fix
1. **`agent/runtime.py` Multi-Tool 1:1 Folding:**
   Updated `fold_tool_reports` and inline `_take_tool_calls` to iterate over all envelopes and generate an individual `role: "tool"` transcript message matching each tool call's ID. Added synthetic fallback messages if any call was omitted by the client.
2. **`AgentRunDriver.kt` Exponential Backoff & Honest Messaging:**
   Added 3x exponential backoff retries for transient 502/network interruptions. Updated fallback message to report honestly: *"Aura executed the requested actions on the device, but the connection to the server was interrupted. The task state was preserved."*
3. **Vision Integration (`tools/providers/android_provider.py`):**
   Integrated `CloudVisionProcessor` into `Screenshot.execute` to pass base64 screen frames through Gemini Vision (`gemini-3.6-flash`), providing rich natural language scene understanding.

---

## 11. Full Autonomy Lock Verification

- **Invariant:** `full_autonomy_enabled = false` MUST remain locked. STATE 3 MUST NEVER be enabled without explicit human intervention.
- **Audit Result:** Verified `full_autonomy_enabled` remains `False` across all configurations and models. All security gates and human-in-the-loop confirmation mechanisms are intact.

---

## 12. Verification & Test Evidence

### P4 Test Matrix
| Test Name | File | Result | Duration |
|:---|:---|:---:|:---:|
| `test_node_identity` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.04s |
| `test_event_creation_and_hash` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.05s |
| `test_event_persistence` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.06s |
| `test_outbox_lifecycle` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.08s |
| `test_timeout_preserves_event` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.06s |
| `test_idempotency_and_duplicate_delivery` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.07s |
| `test_conflict_detection_and_quarantine` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.06s |
| `test_experience_replication` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.08s |
| `test_tool_replication` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.07s |
| `test_cursor_resume` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.05s |
| `test_bidirectional_sync_convergence` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.12s |
| `test_large_backlog_1000_events` | `tests/test_p4_distributed_sync.py` | **PASSED** | 1.84s |
| `test_network_chaos_packet_loss_recovery` | `tests/test_p4_distributed_sync.py` | **PASSED** | 0.09s |
| `test_sync_register_node` | `tests/test_p4_sync_routes.py` | **PASSED** | 0.04s |
| `test_sync_push_events_valid` | `tests/test_p4_sync_routes.py` | **PASSED** | 0.05s |
| `test_sync_push_events_corrupt_hash_quarantined` | `tests/test_p4_sync_routes.py` | **PASSED** | 0.06s |
| `test_sync_pull_events` | `tests/test_p4_sync_routes.py` | **PASSED** | 0.06s |
| `test_sync_ack_events` | `tests/test_p4_sync_routes.py` | **PASSED** | 0.05s |
| `test_sync_status_diagnostics` | `tests/test_p4_sync_routes.py` | **PASSED** | 0.04s |
| `test_sync_conflicts_and_resolve` | `tests/test_p4_sync_routes.py` | **PASSED** | 0.06s |
| `test_multiple_device_reports_fold_cleanly` | `tests/test_agent_runtime.py` | **PASSED** | 0.05s |

### Android Companion Verification
- Android Unit Tests: **414 / 414 PASSED** (`.\gradlew.bat testDebugUnitTest`)
- APK Build: `assembleDebug` completed successfully (`app-debug.apk`)
- Target Device Deployment: APK installed via ADB to OPPO Reno6 5G (`IBCQMB4PTGNZJVTO`).

### Generated Evidence Artifacts
- `D:\AURA\artifacts\p4_sync_validation.json`
- `D:\AURA\artifacts\p4_network_chaos.json`
- `D:\AURA\artifacts\p4_convergence.json`
- `D:\AURA\artifacts\p4_security_audit.json`

---

## 13. Conclusion

AURA Phase 4 (Distributed Continuity & Always-Sync) is fully implemented, mathematically and forensically validated, hardened against network chaos and malicious payloads, and deployed to physical hardware. The repository is left in an operational, robust, and cleanly synchronized state.
