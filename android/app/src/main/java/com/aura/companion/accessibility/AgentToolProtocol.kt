package com.aura.companion.accessibility

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * The structured tool protocol - the device half of the agent
 * migration (PARTS 1, 2, 8).
 *
 * This file retires `AgentActionParser` and its `KNOWN_ACTIONS` string
 * matching. Under the new contract the server's agent runtime sends a
 * [ToolCallDirective] - a named tool plus plain-data arguments, already
 * schema-validated against the declared catalogue on the server side -
 * and this device answers with a [ToolResultReport] whose success,
 * failure codes and postcondition evidence are fields, never prose.
 *
 * Nothing here parses model output. There is no action string to parse:
 * if a name is not in [DeviceToolCatalog], the failure is
 * UNKNOWN_TOOL and the reasoning layer corrects itself from that code.
 */

@Serializable
data class ToolCallDirective(
    @SerialName("tool_call_id") val toolCallId: String,
    val tool: String,
    val arguments: JsonObject = JsonObject(emptyMap()),
)

@Serializable
data class ToolError(
    val code: String,
    val message: String,
)

/**
 * The PART 8 envelope, as this device fills it in.
 *
 * `postcondition` carries verification evidence when the executor could
 * check one (foreground package after a launch, text in a field after
 * typing). Its absence or an unmet verdict is what stops the server
 * runtime from accepting a completion claim - which is exactly why the
 * executor must fill it honestly rather than optimistically.
 */
@Serializable
data class ToolResultReport(
    @SerialName("tool_call_id") val toolCallId: String,
    @SerialName("call_id") val callId: String = "",
    val tool: String,
    val arguments: JsonObject = JsonObject(emptyMap()),
    val ok: Boolean,
    val result: JsonObject? = null,
    val error: ToolError? = null,
    val postcondition: JsonObject? = null,
    @SerialName("observation_id") val observationId: String? = null,
    /**
     * What the device saw while answering, with its own identity.
     *
     * Carried on the report rather than inferred by the server: only the
     * handset knows when it looked and at what, and PARTS 7 and 12
     * require a caller to be able to tell one observation from the next
     * without trusting that two calls in a row saw different screens.
     */
    val observation: ObservationPayload? = null,
)

/**
 * One thing the device observed, identified and timestamped at the
 * moment of observation.
 *
 * `contentHash` is what makes staleness decidable rather than assumed:
 * two captures of an unchanged screen hash the same, and a changed
 * screen cannot hash the same, so "is this the frame I already had?" is
 * a comparison instead of a guess.
 */
@Serializable
data class ObservationPayload(
    @SerialName("observation_id") val observationId: String,
    val kind: String,
    val source: String = "android_device",
    @SerialName("observed_at") val observedAt: Double,
    @SerialName("content_hash") val contentHash: String = "",
    val data: JsonObject = JsonObject(emptyMap()),
)

/**
 * The catalogue of tools this device can execute, with the argument
 * shape each insists on.
 *
 * The direct replacement for `KNOWN_ACTIONS`: instead of a set of
 * accepted strings, each entry knows its required and optional
 * argument names, so validation happens before execution and produces
 * machine-readable reasons. A new device capability is one entry here
 * plus its executor branch - the same declaration the server's registry
 * mirrors when it builds function-calling schemas.
 */
object DeviceToolCatalog {

    // Failure codes deliberately live in ONE place -
    // AccessibilityToolDispatcher's companion - because two copies of a
    // wire vocabulary are two things that can disagree. This object
    // decides only whether a call is valid; naming the refusal is the
    // dispatcher's job.

    data class ToolSpec(
        val name: String,
        val required: Set<String>,
        val optional: Set<String>,
        /** True when the tool changes device state. */
        val mutating: Boolean,
    )

    val TOOLS: Map<String, ToolSpec> = listOf(
        ToolSpec("android.get_foreground_app", emptySet(), emptySet(), false),
        ToolSpec("android.get_ui_tree", emptySet(), setOf("max_depth"), false),
        ToolSpec("android.find_node", setOf("text"), emptySet(), false),
        ToolSpec("android.screenshot", emptySet(), emptySet(), false),
        ToolSpec("android.tap", emptySet(), setOf("node_id", "text"), true),
        ToolSpec("android.long_press", emptySet(), setOf("node_id", "text"), true),
        ToolSpec("android.swipe", setOf("direction"), emptySet(), true),
        ToolSpec("android.type_text", setOf("text"), setOf("node_id"), true),
        ToolSpec("android.press_key", setOf("key"), emptySet(), true),
        ToolSpec("android.back", emptySet(), emptySet(), true),
        ToolSpec("android.home", emptySet(), emptySet(), true),
        ToolSpec("android.launch_app", setOf("package"), emptySet(), true),
        ToolSpec("android.wait_for", setOf("condition"),
            setOf("timeout_ms"), false),
        ToolSpec("android.verify", setOf("check"), emptySet(), false),
    ).associateBy { it.name }

    sealed interface Validation {
        /** Execute it, and hold the executor to the postcondition. */
        data class Ok(val spec: ToolSpec) : Validation

        data class UnknownTool(val tool: String) : Validation

        data class BadArguments(val reason: String) : Validation
    }

    fun validate(directive: ToolCallDirective): Validation {
        return validate(directive.tool, directive.arguments)
    }

    fun validate(tool: String, arguments: JsonObject): Validation {

        val spec = TOOLS[tool] ?: return Validation.UnknownTool(tool)

        val provided = arguments.keys

        val missing = spec.required.filterNot { it in provided }

        if (missing.isNotEmpty()) {
            return Validation.BadArguments(
                "$tool requires ${missing.joinToString(", ")}"
            )
        }

        // An argument outside required+optional is a disagreement about
        // the contract, not noise: executing with it silently ignored
        // would make the reasoning layer believe something happened
        // that did not.
        val foreign = provided.filterNot {
            it in spec.required || it in spec.optional
        }

        if (foreign.isNotEmpty()) {
            return Validation.BadArguments(
                "$tool does not accept ${foreign.joinToString(", ")}"
            )
        }

        return Validation.Ok(spec)
    }

    /** True when a failed/unverified result must block completion. */
    fun isMutating(tool: String): Boolean = TOOLS[tool]?.mutating ?: false
}