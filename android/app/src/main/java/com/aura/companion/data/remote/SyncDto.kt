package com.aura.companion.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * Wire DTOs for AURA P2/P4 Distributed Synchronization.
 * Exactly matches `server/routes/sync.py` and `core/sync/models.py`.
 */

@Serializable
data class RegisterNodeRequestDto(
    @SerialName("node_id") val nodeId: String,
    @SerialName("node_type") val nodeType: String = "ANDROID",
    @SerialName("installation_id") val installationId: String = "",
    val metadata: Map<String, JsonElement> = emptyMap(),
)

@Serializable
data class RegisterNodeResponseDto(
    val status: String = "ok",
    @SerialName("node_id") val nodeId: String = "",
    @SerialName("last_seen") val lastSeen: String = "",
)

@Serializable
data class SyncEventDto(
    @SerialName("event_id") val eventId: String,
    @SerialName("origin_node_id") val originNodeId: String,
    @SerialName("event_type") val eventType: String,
    @SerialName("entity_type") val entityType: String = "",
    @SerialName("entity_id") val entityId: String = "",
    @SerialName("schema_version") val schemaVersion: Int = 1,
    @SerialName("created_at") val createdAt: String,
    @SerialName("logical_sequence") val logicalSequence: Long = 0,
    val payload: JsonObject = JsonObject(emptyMap()),
    @SerialName("payload_hash") val payloadHash: String = "",
    @SerialName("parent_event_id") val parentEventId: String? = null,
    val provenance: JsonObject = JsonObject(emptyMap()),
    @SerialName("received_at") val receivedAt: String? = null,
)

@Serializable
data class PushEventsRequestDto(
    @SerialName("node_id") val nodeId: String,
    val events: List<SyncEventDto>,
)

@Serializable
data class PushEventsResponseDto(
    val status: String,
    val acknowledged: List<String> = emptyList(),
    val conflicts: List<JsonObject> = emptyList(),
    @SerialName("received_count") val receivedCount: Int = 0,
)

@Serializable
data class PullEventsResponseDto(
    val status: String = "ok",
    val events: List<SyncEventDto> = emptyList(),
    val cursor: Long = 0,
    @SerialName("has_more") val hasMore: Boolean = false,
)

@Serializable
data class AckEventsRequestDto(
    @SerialName("node_id") val nodeId: String,
    @SerialName("event_ids") val eventIds: List<String>,
)

@Serializable
data class AckEventsResponseDto(
    val status: String = "ok",
    @SerialName("acknowledged_count") val acknowledgedCount: Int = 0,
)

@Serializable
data class SyncStatusResponseDto(
    @SerialName("node_id") val nodeId: String = "",
    @SerialName("node_type") val nodeType: String = "",
    @SerialName("sync_state") val syncState: String = "",
    @SerialName("pending_outbox_events") val pendingOutboxEvents: Int = 0,
    @SerialName("acknowledged_outbox_events") val acknowledgedOutboxEvents: Int = 0,
    @SerialName("total_local_events") val totalLocalEvents: Int = 0,
    @SerialName("quarantined_conflicts") val quarantinedConflicts: Int = 0,
    val cursors: List<JsonObject> = emptyList(),
    @SerialName("last_seen") val lastSeen: String = "",
)

@Serializable
data class ResolveConflictRequestDto(
    val resolution: String = "MERGED_MANUAL",
)
