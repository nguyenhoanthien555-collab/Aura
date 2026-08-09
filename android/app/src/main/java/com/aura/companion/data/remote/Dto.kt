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
