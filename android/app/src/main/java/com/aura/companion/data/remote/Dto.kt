package com.aura.companion.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * Wire types.
 *
 * These mirror `server/models.py` and the response models in
 * `server/routes/`. They are deliberately separate from the domain types
 * in `data.model`: the wire format belongs to the server, the domain
 * belongs to the app, and a change to one should not silently reshape the
 * other.
 */

@Serializable
data class ChatRequestDto(
    @SerialName("session_id") val sessionId: String? = null,
    val message: String,
    val context: JsonObject = JsonObject(emptyMap()),
    val metadata: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class ChatResponseDto(
    @SerialName("session_id") val sessionId: String,
    val reply: String,
    @SerialName("message_id") val messageId: String,
    val metadata: Map<String, JsonElement> = emptyMap(),
)

/**
 * The single frame a streaming client sends after the socket opens.
 *
 * The session id is not in here: the WebSocket route takes it from the
 * query string at handshake time (`server/routes/ws_chat.py`), so putting
 * it in the body as well would create two sources of truth for which
 * conversation this is.
 */
@Serializable
data class StreamRequestDto(
    val message: String,
    val context: JsonObject = JsonObject(emptyMap()),
    val metadata: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class HealthDto(
    val status: String,
    val version: String = "",
    @SerialName("uptime_seconds") val uptimeSeconds: Double = 0.0,
    val runtime: Map<String, String> = emptyMap(),
)

@Serializable
data class ScreenRequestDto(
    @SerialName("session_id") val sessionId: String = "",
    @SerialName("device_id") val deviceId: String = "",
    val application: String? = null,
    @SerialName("package") val packageName: String? = null,
    @SerialName("screen_text") val screenText: String? = null,
    @SerialName("accessibility_context") val accessibilityContext: JsonObject? = null,
    val timestamp: Double? = null,
)

@Serializable
data class DecisionDto(
    @SerialName("should_notify") val shouldNotify: Boolean = false,
    val reason: String = "",
    val priority: String = "normal",
    val message: String = "",
    val confidence: Double = 0.0,
    val cooldown: Double = 0.0,
)

@Serializable
data class ScreenResponseDto(
    @SerialName("session_id") val sessionId: String = "",
    val status: String = "",
    val accepted: Boolean = false,
    @SerialName("observation_id") val observationId: String? = null,
    val decision: DecisionDto? = null,
)

/**
 * The reply to a multipart screenshot upload.
 *
 * A concrete type rather than a `Map<String, JsonElement>`: Retrofit's
 * converter resolves a serializer from the erased Java `Type`, and a
 * generic map is exactly the shape that resolution is least reliable for.
 */
@Serializable
data class UploadResponseDto(
    @SerialName("session_id") val sessionId: String = "",
    val status: String = "",
    val accepted: Boolean = false,
    @SerialName("size_bytes") val sizeBytes: Long = 0,
    val decision: DecisionDto? = null,
)

@Serializable
data class NotificationDto(
    @SerialName("notification_id") val notificationId: String,
    val message: String,
    val reason: String = "",
    val priority: String = "normal",
    val confidence: Double = 0.0,
    val source: String = "",
    @SerialName("created_at") val createdAt: Double = 0.0,
)

@Serializable
data class NotificationsResponseDto(
    val notifications: List<NotificationDto> = emptyList(),
    val count: Int = 0,
    @SerialName("companion_enabled") val companionEnabled: Boolean = false,
)

// ---------------------------------------------------------------------------
// Agent tool protocol - the structured migration's wire types.
//
// These mirror `server/routes/agent.py` and `server/routes/device.py`.
// Nothing here is parsed out of prose in either direction: directives
// arrive as named tools with typed arguments, results leave as
// envelopes with verdicts and error codes.
// ---------------------------------------------------------------------------

@Serializable
data class ObservationDto(
    val kind: String,
    val source: String = "device",
    val data: JsonObject = JsonObject(emptyMap()),
    @SerialName("observation_id") val observationId: String = "",
    @SerialName("observed_at") val observedAt: Double = 0.0,
    @SerialName("content_hash") val contentHash: String = "",
)

/** One executed tool call, as the server's runtime expects it back. */
@Serializable
data class ToolResultEnvelopeDto(
    @SerialName("tool_call_id") val toolCallId: String,
    @SerialName("call_id") val callId: String = "",
    val tool: String,
    val arguments: JsonObject = JsonObject(emptyMap()),
    val ok: Boolean,
    val result: JsonObject? = null,
    val error: JsonObject? = null,
    val postcondition: JsonObject? = null,
    @SerialName("observation_id") val observationId: String? = null,
)

@Serializable
data class AgentStepRequestDto(
    @SerialName("session_id") val sessionId: String,
    /** Required only on the first step of a run. */
    val goal: String = "",
    @SerialName("run_id") val runId: String = "",
    val observations: List<ObservationDto> = emptyList(),
    @SerialName("tool_results") val toolResults: List<ToolResultEnvelopeDto> =
        emptyList(),
)

@Serializable
data class ToolCallDto(
    @SerialName("tool_call_id") val toolCallId: String,
    val tool: String,
    val arguments: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class AgentDirectiveDto(
    /** "tool_calls" or "final". */
    val type: String = "",
    val text: String? = null,
    @SerialName("tool_calls") val toolCalls: List<ToolCallDto> = emptyList(),
)

/** The run snapshot every step response carries. */
@Serializable
data class AgentRunSnapshotDto(
    @SerialName("run_id") val runId: String = "",
    @SerialName("task_id") val taskId: String = "",
    @SerialName("session_id") val sessionId: String = "",
    val goal: String = "",
    val status: String = "",
    @SerialName("stop_reason") val stopReason: String? = null,
    @SerialName("stop_detail") val stopDetail: String = "",
    val rounds: Int = 0,
    @SerialName("tool_call_count") val toolCallCount: Int = 0,
    val directive: AgentDirectiveDto? = null,
)

@Serializable
data class DevicePollRequestDto(
    @SerialName("device_id") val deviceId: String = "",
    @SerialName("timeout_s") val timeoutS: Double = 0.0,
    /** Runtime facts used by the server capability registry. */
    val capabilities: Map<String, DeviceCapabilityStatusDto> = emptyMap(),
)

@Serializable
data class DeviceCapabilityStatusDto(
    val state: String = "UNKNOWN",
    val healthy: Boolean = false,
    val reason: String = "",
    val permissions: Map<String, Boolean> = emptyMap(),
)

/** One queued invocation from `/api/device/poll`. */
@Serializable
data class DeviceInvocationDto(
    @SerialName("invocation_id") val invocationId: String,
    @SerialName("run_id") val runId: String = "",
    @SerialName("tool_call_id") val toolCallId: String = "",
    val tool: String,
    val arguments: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class DevicePollResponseDto(
    val invocations: List<DeviceInvocationDto> = emptyList(),
)

@Serializable
data class DeviceResultReportDto(
    @SerialName("invocation_id") val invocationId: String,
    val ok: Boolean = false,
    val result: JsonObject? = null,
    val error: JsonObject? = null,
    val postcondition: JsonObject? = null,
    @SerialName("observation_id") val observationId: String? = null,
    /**
     * What the device saw while answering.
     *
     * The gateway forwards it verbatim to the agent runtime, which mints
     * its own observation record from it - so the payload is how a tool
     * result on the polling transport carries the same evidence a
     * directly-executed one does.
     */
    val observation: JsonObject? = null,
)

@Serializable
data class DeviceResultSubmissionDto(
    @SerialName("device_id") val deviceId: String = "",
    val reports: List<DeviceResultReportDto> = emptyList(),
)

@Serializable
data class DeviceResultAckDto(
    val accepted: Int = 0,
    val rejected: List<String> = emptyList(),
)
