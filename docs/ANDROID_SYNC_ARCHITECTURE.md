# AURA Android-Native Distributed Event Synchronization Architecture

**Author:** AURA Distributed Systems Team  
**Phase:** Phase 2 — Android Native Event Sync & Distributed Continuity  
**Target Topology:** Laptop Brain <---> Render Durable Relay / API <---> Android Companion Device  
**Status:** IMPLEMENTED & FORENSICALLY VERIFIED (Unit & Contract Test Suite)  

---

## 1. Executive Summary & Topology

AURA operates across a heterogeneous multi-device topology where a primary compute engine (Laptop Brain running local LLM inference) communicates with an Android mobile device (capturing screen state, executing accessibility tool actions, and handling human interaction) via a durable cloud relay (Render API / SQLite persistence).

```text
                    INTERNET
                       │
             ┌─────────▼─────────┐
             │       RENDER      │
             │ Durable Relay/API │
             │ Sync + AgentRuns  │
             └───────┬─────┬─────┘
                     │     │
               HTTPS │     │ HTTPS
                     │     │
              ┌──────▼─────┴──────┐
              │                   │
     ┌────────▼────────┐ ┌────────▼────────┐
     │   LAPTOP AURA   │ │ ANDROID AURA    │
     │   Primary Brain │ │ Companion Dev   │
     │   Local LLM     │ │ Sensor / Exec   │
     └─────────────────┘ └─────────────────┘
```

Prior to Phase 2, the Android companion device relied solely on polling tool invocations via `/api/device/poll`. Phase 2 implements and proves the bi-directional event synchronization layer (`SyncClient`), durable file outbox, sequence cursor tracking, cryptographic payload hashing, and idempotent inbox deduplication.

---

## 2. Core Android-Native Components

All Kotlin sync components reside in `com.aura.companion.sync` inside `android/app/src/main/java/com/aura/companion/sync/`.

### 2.1 CanonicalJson (`CanonicalJson.kt`)
To guarantee cryptographic parity across languages, Android generates deterministic JSON matching Python's `canonical_json(data)` bit-for-bit:
- Object keys are recursively sorted alphabetically.
- No whitespace around colons or commas (separators `,` and `:`).
- Standard JSON string escaping without ASCII-only Unicode encoding.
- SHA-256 hexadecimal hash calculation matches Python `hashlib.sha256(canonical.encode('utf-8')).hexdigest()`.

### 2.2 CursorStore (`CursorStore.kt`)
- Interface: `CursorStore` with implementations `FileCursorStore`.
- Persists the latest sequence number received from the Render relay (`cursor_{node_id}.txt`).
- **Critical Safety Invariant:** Cursor advances **ONLY** after successful local event application (`applied == true`).

### 2.3 EventOutbox (`EventOutbox.kt`)
- Interface: `EventOutbox` with implementation `FileEventOutbox`.
- Backed by an append-only, crash-safe JSON Lines journal (`outbox_journal.jsonl`).
- State machine: `PENDING -> SENDING -> ACKNOWLEDGED` (or `QUARANTINED` on schema conflicts).
- **Durable Retention Invariant:** Events in the outbox are **NEVER** dropped on network failures, HTTP 500/503 errors, or socket timeouts. They revert to `PENDING` with retry backoff and persist across complete device reboots.

### 2.4 EventInbox (`EventInbox.kt`)
- Interface: `EventInbox` with implementation `FileEventInbox`.
- Maintains a durable journal of applied event IDs and their corresponding cryptographic payload hashes (`inbox_journal.jsonl`).
- **Idempotent Duplicate Suppression:** Arriving events with previously applied IDs and matching hashes return `InboxApplyResult.IdempotentDuplicate`.
- **Tampering & Conflict Detection:** Arriving events with invalid hashes or conflicting payloads for an existing ID return `InboxApplyResult.Conflict` and are quarantined.

### 2.5 SyncClient (`SyncClient.kt`)
Orchestrates the entire synchronization loop:
1. `registerNode()`: Registers Android device identity and hardware metadata with Render relay.
2. `pushPending()`: Retrieves pending outbox events, transmits them via `POST /api/sync/events/push`, and updates outbox state based on server response.
3. `pullIncoming()`: Queries `GET /api/sync/events/pull?after_sequence={cursor}`, verifies payload hashes, suppresses duplicates, dispatches events to local domain handlers, advances the cursor upon successful application, and sends batch ACK via `POST /api/sync/events/ack`.
4. `syncCycle()`: Executes complete push/pull cycle in a single atomic routine.

---

## 3. Wire Protocol & REST Specifications

### 3.1 `POST /api/sync/register`
- **Request:**
  ```json
  {
    "node_id": "android-cph2251-uuid",
    "node_type": "ANDROID",
    "installation_id": "android-cph2251-uuid",
    "metadata": {
      "model": "OPPO Reno6 5G",
      "os_version": "Android 13"
    }
  }
  ```
- **Response:** `{"status": "ok", "node_id": "...", "last_seen": "..."}`

### 3.2 `POST /api/sync/events/push`
- **Request:**
  ```json
  {
    "node_id": "android-cph2251-uuid",
    "events": [
      {
        "event_id": "evt-719c-001",
        "origin_node_id": "android-cph2251-uuid",
        "event_type": "SCREEN_STATE",
        "entity_type": "screen",
        "entity_id": "com.android.settings",
        "schema_version": 1,
        "created_at": "2026-09-17T12:00:00Z",
        "logical_sequence": 1,
        "payload": {"screen": "com.android.settings/.Settings"},
        "payload_hash": "19658421d81bc7cd3f3db907844edff936b2f045020789b554fe81b0b2f75410",
        "parent_event_id": null,
        "provenance": {"device": "OPPO Reno6"}
      }
    ]
  }
  ```
- **Response:**
  ```json
  {
    "status": "ok",
    "acknowledged": ["evt-719c-001"],
    "conflicts": [],
    "received_count": 1
  }
  ```

### 3.3 `GET /api/sync/events/pull`
- **Query Parameters:** `node_id={node_id}&after_sequence={cursor}&limit=100`
- **Response:**
  ```json
  {
    "status": "ok",
    "events": [...],
    "cursor": 42,
    "has_more": false
  }
  ```

### 3.4 `POST /api/sync/events/ack`
- **Request:** `{"node_id": "...", "event_ids": ["evt-719c-001"]}`
- **Response:** `{"status": "ok", "acknowledged_count": 1}`

---

## 4. Offline Resilience & Network Flapping

1. **Disconnected Operation:** When Android loses network connectivity, UI events and tool reports are journaled to `FileEventOutbox`. The companion continues local operations without crashing or hanging.
2. **Reconnection Convergence:** Upon connectivity restoration, `pushPending()` flushes the accumulated backlog in chronological order. The server idempotency ledger safely processes retransmitted packets without duplication.
3. **Flapping Resistance:** In simulated 10-cycle flapping tests (intermittent disconnects during mid-transmission), zero events were dropped, and the outbox drained to 0 pending once stability returned.

---

## 5. Physical Validation & Ground Truth Gate

In strict adherence to forensic honesty:
- **Unit & MockWebServer Verification:** 100% verified across Kotlin JUnit tests and Python FastAPI integration tests.
- **Physical Hardware Status:** `PHYSICAL ANDROID TEST = BLOCKED (0 devices attached to adb host)`.
- **Cross-Carrier Network Status:** `TRUE 4G/5G CROSS-NETWORK = NOT VERIFIED (requires physically attached provisioned SIM hardware)`.
- **Safety Invariant:** `full_autonomy_enabled == False` strictly preserved across all modules and tests.
