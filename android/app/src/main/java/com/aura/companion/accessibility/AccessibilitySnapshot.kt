package com.aura.companion.accessibility

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

@Serializable
data class DeviceState(
    val width: Int,
    val height: Int
)

@Serializable
data class AppInfo(
    @SerialName("package") val packageName: String,
    val activity: String? = null,
    val label: String? = null
)

@Serializable
data class AccessibilityNode(
    val id: String,
    val text: String?,
    @SerialName("content_description") val contentDescription: String?,
    @SerialName("class_name") val className: String?,
    val clickable: Boolean,
    val enabled: Boolean = true,
    val visible: Boolean = true,
    val bounds: List<Int>, // [left, top, right, bottom]
    val role: String? = null,
    val scrollable: Boolean = false,
    @SerialName("long_clickable") val longClickable: Boolean = false,
    val editable: Boolean = false,
    val selected: Boolean = false,
    val checked: Boolean = false,
    val focused: Boolean = false
)

@Serializable
data class AccessibilitySnapshot(
    val device: DeviceState,
    val app: AppInfo,
    @SerialName("accessibility_tree") val accessibilityTree: Map<String, AccessibilityNode>,
    @SerialName("screenshot_available") val screenshotAvailable: Boolean = false,
    @SerialName("user_request") val userRequest: String? = null,
    @SerialName("last_action_error") val lastActionError: String? = null,
    @SerialName("completed_actions") val completedActions: List<String> = emptyList()
)


@Serializable
data class AgentAction(
    val action: String,
    @SerialName("node_id") val nodeId: String? = null,
    val text: String? = null,
    val direction: String? = null,
    @SerialName("package") val packageName: String? = null,
    val message: String? = null
)
