package com.aura.companion.accessibility

import android.graphics.BitmapFactory
import android.view.accessibility.AccessibilityNodeInfo
import com.aura.companion.screen.AccessibilityScreenshotCapture
import com.aura.companion.data.remote.DeviceCapabilityStatusDto
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.put

/**
 * The device half of the tool protocol (PARTS 4-12).
 *
 * One dispatcher, one implementation, two transports: the polling
 * gateway hands it invocations from `/api/device/poll`, and the
 * [AgentRunDriver] hands it directives from `/api/agent/step`. Both
 * arrive as [ToolCallDirective]; both leave as [ToolResultReport].
 *
 * Execution stands on primitives the old loop already proved on
 * hardware - `executeActionWithRecovery` (SafetyGuard included),
 * the settle verifications in the service companion, and
 * [AccessibilityNodeSerializer] for fresh trees. Nothing here
 * re-implements a gesture.
 *
 * It implements [DeviceToolExecutor] rather than defining its own entry
 * point, because that interface is the seam JVM tests substitute: an
 * executor tests can script and the phone can satisfy is what makes the
 * driver testable without hardware.
 */
class AccessibilityToolDispatcher(
    private val service: AuraAccessibilityService,
) : DeviceToolExecutor, DeviceCapabilityReporter {

    override fun capabilityStatus(): Map<String, DeviceCapabilityStatusDto> {
        val basePermissions = mapOf("android.accessibility" to true)
        val statuses = listOf(
            "android.foreground_app", "android.ui_tree", "android.ui_search",
            "android.tap", "android.long_press", "android.swipe",
            "android.text_input", "android.key_input", "android.back",
            "android.home", "android.app_launch", "android.wait_for",
            "android.verification",
        ).associateWith {
            DeviceCapabilityStatusDto(
                state = "AVAILABLE",
                healthy = true,
                reason = "Aura accessibility service is connected",
                permissions = basePermissions,
            )
        }.toMutableMap()

        val captureSupported = AccessibilityScreenshotCapture(service).isSupported
        val captureAllowed = service.screenshotToolAllowed()
        statuses["android.screen_capture"] = DeviceCapabilityStatusDto(
            state = when {
                !captureSupported -> "UNAVAILABLE"
                !captureAllowed -> "BLOCKED_PERMISSION"
                else -> "AVAILABLE"
            },
            healthy = captureSupported,
            reason = when {
                !captureSupported -> "this Android version cannot take screenshots"
                !captureAllowed -> "screen observation and screenshot upload are switched off in Aura"
                else -> "accessibility screenshot capture is available"
            },
            permissions = basePermissions + ("android.screen_capture" to captureAllowed),
        )
        return statuses
    }

    companion object {
        const val TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
        const val INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
        const val BLOCKED_PERMISSION = "BLOCKED_PERMISSION"
        const val CAPABILITY_UNHEALTHY = "CAPABILITY_UNHEALTHY"
        const val CAPABILITY_UNKNOWN = "CAPABILITY_UNKNOWN"
        const val CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
        const val EXECUTION_FAILED = "EXECUTION_FAILED"
        const val ROOT_UNAVAILABLE = "ACCESSIBILITY_ROOT_UNAVAILABLE"
        const val NODE_NOT_FOUND = "NODE_NOT_FOUND"
        const val CAPTURE_UNAVAILABLE = "SCREEN_CAPTURE_UNAVAILABLE"
        const val BLOCKED = "BLOCKED_BY_SAFETY_GUARD"
        const val TIMEOUT = "TIMEOUT"

        private val CAPABILITY_BY_TOOL = mapOf(
            "android.get_foreground_app" to "android.foreground_app",
            "android.get_ui_tree" to "android.ui_tree",
            "android.find_node" to "android.ui_search",
            "android.screenshot" to "android.screen_capture",
            "android.tap" to "android.tap",
            "android.long_press" to "android.long_press",
            "android.swipe" to "android.swipe",
            "android.type_text" to "android.text_input",
            "android.press_key" to "android.key_input",
            "android.back" to "android.back",
            "android.home" to "android.home",
            "android.launch_app" to "android.app_launch",
            "android.wait_for" to "android.wait_for",
            "android.verify" to "android.verification",
        )
    }

    /**
     * Validate, execute, observe. Never throws at the transport: every
     * failure - including a malformed directive - comes back as a
     * structured report so the reasoning layer can correct itself.
     *
     * The one exception is [CancellationException], which is rethrown:
     * swallowing it into a report would leave a cancelled run's coroutine
     * alive and answering, which is the orphaned-operation failure the
     * cancellation contract exists to prevent.
     */
    override suspend fun execute(
        directive: ToolCallDirective,
    ): ToolResultReport {
        val capabilityId = CAPABILITY_BY_TOOL[directive.tool]
        if (capabilityId != null) {
            val status = capabilityStatus()[capabilityId]
            val state = status?.state ?: "UNKNOWN"
            if (state != "AVAILABLE") {
                val code = when (state) {
                    "BLOCKED_PERMISSION" -> BLOCKED_PERMISSION
                    "UNHEALTHY" -> CAPABILITY_UNHEALTHY
                    "UNKNOWN" -> CAPABILITY_UNKNOWN
                    else -> CAPABILITY_UNAVAILABLE
                }
                return failure(
                    directive,
                    code,
                    status?.reason
                        ?: "capability $capabilityId is not available",
                )
            }
        }

        return when (val validation = DeviceToolCatalog.validate(directive)) {
            is DeviceToolCatalog.Validation.UnknownTool ->
                failure(directive, TOOL_NOT_FOUND,
                    "this device has no tool ${directive.tool}")

            is DeviceToolCatalog.Validation.BadArguments ->
                failure(directive, INVALID_ARGUMENTS, validation.reason)

            is DeviceToolCatalog.Validation.Ok -> try {
                dispatch(directive)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                failure(directive, EXECUTION_FAILED,
                    error.message ?: "${directive.tool} failed")
            }
        }
    }

    // ------------------------------------------------------------------
    // Dispatch table
    // ------------------------------------------------------------------

    private suspend fun dispatch(
        directive: ToolCallDirective,
    ): ToolResultReport = when (directive.tool) {
        "android.get_foreground_app" -> getForegroundApp(directive)
        "android.get_ui_tree" -> getUITree(directive)
        "android.find_node" -> findNode(directive)
        "android.screenshot" -> screenshot(directive)
        "android.tap", "android.long_press",
        "android.swipe", "android.type_text",
        "android.press_key", "android.back", "android.home",
        "android.launch_app" -> mutate(directive)
        "android.wait_for" -> waitFor(directive)
        "android.verify" -> verify(directive)
        else -> failure(directive, TOOL_NOT_FOUND,
            "declared but not executable: ${directive.tool}")
    }

    // ------------------------------------------------------------------
    // Report builders
    // ------------------------------------------------------------------

    /**
     * One observation, identified and timestamped at capture.
     *
     * `contentHash` defaults to a hash of the payload so that every
     * observation carries one: a caller comparing two reports can always
     * decide whether the device looked at the same thing twice, even for
     * kinds with no natural fingerprint of their own.
     */
    private fun observation(
        kind: String,
        data: JsonObject,
        contentHash: String = "",
    ): ObservationPayload = ObservationPayload(
        observationId = ObservationIds.newObservationId(),
        kind = kind,
        source = "android_device",
        observedAt = ObservationIds.nowEpochSeconds(),
        contentHash = contentHash.ifEmpty {
            ObservationIds.hashOf(data.toString())
        },
        data = data,
    )

    private fun success(
        directive: ToolCallDirective,
        result: JsonObject,
        observation: ObservationPayload,
        postcondition: JsonObject? = null,
    ): ToolResultReport = ToolResultReport(
        toolCallId = directive.toolCallId,
        tool = directive.tool,
        arguments = directive.arguments,
        ok = true,
        result = result,
        postcondition = postcondition,
        observationId = observation.observationId,
        observation = observation,
    )

    private fun failure(
        directive: ToolCallDirective,
        code: String,
        message: String,
        observation: ObservationPayload? = null,
    ): ToolResultReport = ToolResultReport(
        toolCallId = directive.toolCallId,
        tool = directive.tool,
        arguments = directive.arguments,
        ok = false,
        error = ToolError(code = code, message = message),
        observationId = observation?.observationId,
        observation = observation,
    )

    // ------------------------------------------------------------------
    // Reads
    // ------------------------------------------------------------------

    /** PART 6: metadata only, never pixels, never cached. */
    private fun getForegroundApp(
        directive: ToolCallDirective,
    ): ToolResultReport {
        val app = AuraAccessibilityService.currentForegroundApp()
            ?: return failure(directive, CAPABILITY_UNAVAILABLE,
                "no active window; accessibility state unavailable")

        val data = buildJsonObject {
            put("package", app.packageName)
            put("label", app.label)
            put("activity", app.activity ?: "")
        }

        return success(
            directive,
            result = data,
            observation = observation("foreground_app", data),
        )
    }

    /**
     * A fresh serialization of the live tree, or null with the honest
     * ROOT_UNAVAILABLE failure. Shared by tree/find/wait/verify so all
     * four read exactly the same current state.
     */
    private data class FreshTree(
        val nodes: Map<String, AccessibilityNode>,
        val nodeCount: Int,
        val contentHash: String,
    )

    private fun freshTree(): FreshTree? {
        val root = service.rootInActiveWindow ?: return null

        val nodeMap = mutableMapOf<String, AccessibilityNodeInfo>()
        val nodes = AccessibilityNodeSerializer.serialize(root, nodeMap)

        nodeMap.values.forEach { it.recycle() }
        root.recycle()

        return FreshTree(
            nodes = nodes,
            nodeCount = nodes.size,
            contentHash = ObservationIds.hashOf(
                nodes.entries.sortedBy { it.key }
                    .joinToString("|") { (id, node) ->
                        "$id:${node.text}:${node.contentDescription}:" +
                            "${node.bounds}"
                    }
            ),
        )
    }

    /** The observation metadata a tree read carries (PART 7). */
    private fun treeObservation(tree: FreshTree): ObservationPayload =
        observation(
            kind = "accessibility_tree",
            data = buildJsonObject {
                put("node_count", tree.nodeCount)
                put("content_hash", tree.contentHash)
                put("package",
                    AuraAccessibilityService.currentForegroundApp()
                        ?.packageName ?: "")
            },
            contentHash = tree.contentHash,
        )

    private fun textsOf(nodes: Map<String, AccessibilityNode>): Set<String> =
        nodes.values.flatMap { listOfNotNull(it.text, it.contentDescription) }
            .filter { it.isNotBlank() }.toSet()

    /** PART 7: a real hierarchy, or an honest root-unavailable. */
    private fun getUITree(
        directive: ToolCallDirective,
    ): ToolResultReport {
        val tree = freshTree()
            ?: return failure(directive, ROOT_UNAVAILABLE,
                "no active accessibility root right now")

        val observed = treeObservation(tree)

        return success(
            directive,
            result = buildJsonObject {
                put("node_count", tree.nodeCount)
                put("content_hash", tree.contentHash)
                put("nodes", Json.encodeToJsonElement(tree.nodes))
            },
            observation = observed,
        )
    }

    /**
     * PART 8: matched against one FRESH serialization, never an old one.
     *
     * Text, content description, class name and the stable node id are
     * all matched, because the reasoning layer names a target however it
     * saw it in the last tree - and a find that understood only one of
     * those would fail on a perfectly good reference.
     */
    private fun findNode(
        directive: ToolCallDirective,
    ): ToolResultReport {
        val needle = directive.arguments.stringArg("text")
            ?: return failure(directive, INVALID_ARGUMENTS,
                "find_node needs text")

        val tree = freshTree()
            ?: return failure(directive, ROOT_UNAVAILABLE,
                "no active accessibility root right now")

        val observed = treeObservation(tree)

        val match = tree.nodes.entries.firstOrNull { (id, node) ->
            id.equals(needle, ignoreCase = true) ||
                node.text?.contains(needle, ignoreCase = true) == true ||
                node.contentDescription?.contains(
                    needle, ignoreCase = true
                ) == true ||
                node.className?.contains(needle, ignoreCase = true) == true
        }

        if (match == null) {
            // The observation rides along on the failure too: "searched
            // this exact tree and it was not there" is a different claim
            // from "could not look", and only the payload can tell them
            // apart afterwards.
            return failure(directive, NODE_NOT_FOUND,
                "no visible node matching '$needle'",
                observation = observed)
        }

        val node = match.value

        return success(
            directive,
            result = buildJsonObject {
                // A node reference is only meaningful together with the
                // observation that produced it - ids are per-serialization,
                // so a caller holding one from an older tree has to be
                // able to notice that.
                put("node_id", match.key)
                put("observation_id", observed.observationId)
                put("text", node.text ?: "")
                put("content_description", node.contentDescription ?: "")
                put("class_name", node.className ?: "")
                put("clickable", node.clickable)
                put("editable", node.editable)
                put("scrollable", node.scrollable)
                put("bounds", node.bounds.joinToString(","))
            },
            observation = observed,
        )
    }

    /**
     * PART 12: pixels behind the existing capture infrastructure and its
     * gates; an explicit structured error rather than a fabricated frame
     * when capture is switched off, unsupported or fails.
     */
    private suspend fun screenshot(
        directive: ToolCallDirective,
    ): ToolResultReport {

        // The owner's switch is checked first and separately from the
        // capability: "you turned this off" and "this phone cannot" are
        // different answers, and the switch stays authoritative over any
        // tool call.
        if (!service.screenshotToolAllowed()) {
            return failure(directive, CAPTURE_UNAVAILABLE,
                "screenshots are switched off in Aura's settings")
        }

        val capture = AccessibilityScreenshotCapture(service)

        if (!capture.isSupported) {
            return failure(directive, CAPABILITY_UNAVAILABLE,
                "this Android version cannot take screenshots")
        }

        val bytes = capture.capture()
            ?: return failure(directive, CAPTURE_UNAVAILABLE,
                "the framework refused or failed the capture")

        if (bytes.isEmpty()) {
            return failure(directive, CAPTURE_UNAVAILABLE,
                "the capture produced no pixels")
        }

        // Bounds only: decoding the whole frame to read two integers
        // would allocate a full-screen bitmap for nothing.
        val bounds = BitmapFactory.Options().apply {
            inJustDecodeBounds = true
        }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)

        // Hashed over every byte, not a prefix: two JPEGs of different
        // screens share their header, so a prefix hash would call two
        // different frames identical - the precise mistake a freshness
        // check exists to catch.
        val contentHash = ObservationIds.hashOfBytes(bytes)

        val data = buildJsonObject {
            put("format", "jpeg")
            put("size_bytes", bytes.size)
            put("width", bounds.outWidth)
            put("height", bounds.outHeight)
            put("content_hash", contentHash)
        }

        return success(
            directive,
            result = buildJsonObject {
                put("captured", true)
                put("format", "jpeg")
                put("size_bytes", bytes.size)
                put("width", bounds.outWidth)
                put("height", bounds.outHeight)
                put("content_hash", contentHash)
            },
            observation = observation("screenshot", data, contentHash),
        )
    }

    // ------------------------------------------------------------------
    // Mutations - through the service's proven execution primitive, so
    // SafetyGuard and settle verification behave exactly as before.
    // ------------------------------------------------------------------

    private suspend fun mutate(
        directive: ToolCallDirective,
    ): ToolResultReport {
        val action = directive.toAgentAction()
            ?: return failure(directive, CAPABILITY_UNAVAILABLE,
                "${directive.tool} is not executable on this device")

        val before = freshTree()

        if (before == null && action.action != "open_app" &&
            action.action != "back" && action.action != "home"
        ) {
            return failure(directive, ROOT_UNAVAILABLE,
                "no active accessibility root right now")
        }

        val outcome = service.executeActionForProtocol(
            action, before?.nodes ?: emptyMap(),
        )

        // PART 10: a FRESH look after the mutation, every time. The
        // observation describes the screen the action left behind, which
        // is the only evidence the runtime can check a postcondition
        // against - and it is captured after execution, so it cannot be
        // the pre-action screen wearing a new id.
        val after = freshTree()
        val app = AuraAccessibilityService.currentForegroundApp()

        val observed = observation(
            kind = "post_action",
            data = buildJsonObject {
                put("tool", directive.tool)
                put("package", app?.packageName ?: "")
                put("activity", app?.activity ?: "")
                put("node_count", after?.nodeCount ?: 0)
                put("content_hash", after?.contentHash ?: "")
                put("screen_changed",
                    before?.contentHash != after?.contentHash)
            },
            contentHash = after?.contentHash ?: "",
        )

        val result = buildJsonObject {
            put("action", directive.tool.removePrefix("android."))
            put("target", targetOf(action))
            put("package", app?.packageName ?: "")
        }

        return when (outcome) {
            AuraAccessibilityService.ExecutionResult.Blocked ->
                failure(directive, BLOCKED,
                    "blocked by this device's safety guard",
                    observation = observed)

            AuraAccessibilityService.ExecutionResult.Failed ->
                failure(directive, EXECUTION_FAILED,
                    "the action could not be performed",
                    observation = observed)

            AuraAccessibilityService.ExecutionResult.Verified ->
                success(
                    directive,
                    result = result,
                    observation = observed,
                    postcondition = buildJsonObject {
                        put("verified", true)
                        put("observation_id", observed.observationId)
                        put("package", app?.packageName ?: "")
                    },
                )

            AuraAccessibilityService.ExecutionResult.Unverified ->
                success(
                    directive,
                    result = result,
                    observation = observed,
                    postcondition = buildJsonObject {
                        put("verified", false)
                        put("observation_id", observed.observationId)
                        put("note", "executed; state change not observed")
                    },
                )
        }
    }

    private fun targetOf(action: AgentAction): String =
        action.nodeId ?: action.text ?: action.direction
            ?: action.packageName.orEmpty()

    /** PART 11: bounded polling over FRESH samples, never a fixed sleep. */
    private suspend fun waitFor(
        directive: ToolCallDirective,
    ): ToolResultReport {
        val arguments = directive.arguments

        val raw = arguments.stringArg("condition")

        val condition = WaitForConditions.parse(raw)
            ?: return failure(directive, INVALID_ARGUMENTS,
                "unrecognised condition: ${raw ?: ""}")

        // Bounded in both directions: a caller cannot ask for an
        // unbounded wait, and cannot ask for one so long that the
        // transport gives up before the device does.
        val timeoutMs = (arguments.longArg("timeout_ms") ?: DEFAULT_WAIT_MS)
            .coerceIn(MIN_WAIT_MS, MAX_WAIT_MS)

        val startedAt = System.currentTimeMillis()
        val deadline = startedAt + timeoutMs

        val previousActivity: String? =
            AuraAccessibilityService.currentForegroundApp()?.activity

        var polls = 0

        while (true) {
            polls++

            val app = AuraAccessibilityService.currentForegroundApp()
            val tree = freshTree()

            val met = WaitForConditions.evaluate(
                condition = condition,
                foregroundPackage = app?.packageName.orEmpty(),
                activity = app?.activity,
                nodeIds = tree?.nodes?.keys ?: emptySet(),
                texts = textsOf(tree?.nodes ?: emptyMap()),
                previousActivity = previousActivity,
            )

            val observed = observation(
                kind = "wait_for",
                data = buildJsonObject {
                    put("condition", raw ?: "")
                    put("met", met)
                    put("package", app?.packageName ?: "")
                    put("activity", app?.activity ?: "")
                    put("node_count", tree?.nodeCount ?: 0)
                    put("polls", polls)
                },
                contentHash = tree?.contentHash ?: "",
            )

            if (met) {
                return success(
                    directive,
                    result = buildJsonObject {
                        put("met", true)
                        put("condition", raw ?: "")
                        put("package", app?.packageName ?: "")
                        put("waited_ms", System.currentTimeMillis() - startedAt)
                    },
                    observation = observed,
                    postcondition = buildJsonObject {
                        put("verified", true)
                        put("observation_id", observed.observationId)
                    },
                )
            }

            if (System.currentTimeMillis() >= deadline) {
                return failure(directive, TIMEOUT,
                    "condition not met within ${timeoutMs}ms",
                    observation = observed)
            }

            kotlinx.coroutines.delay(POLL_INTERVAL_MS)
        }
    }

    private fun verify(
        directive: ToolCallDirective,
    ): ToolResultReport {
        val raw = directive.arguments.stringArg("check")

        val parsed = VerifyChecks.parse(raw)
            ?: return failure(directive, INVALID_ARGUMENTS,
                "verify needs check=package_is|text_visible|node_exists=<value>")

        val (kind, value) = parsed

        val app = AuraAccessibilityService.currentForegroundApp()
        val tree = freshTree()

        val met = VerifyChecks.evaluate(
            kind = kind,
            value = value,
            foregroundPackage = app?.packageName.orEmpty(),
            nodeIds = tree?.nodes?.keys ?: emptySet(),
            texts = textsOf(tree?.nodes ?: emptyMap()),
        )

        val observed = observation(
            kind = "verify",
            data = buildJsonObject {
                put("check", "$kind=$value")
                put("met", met)
                put("package", app?.packageName ?: "")
                put("node_count", tree?.nodeCount ?: 0)
            },
            contentHash = tree?.contentHash ?: "",
        )

        return success(
            directive,
            result = buildJsonObject {
                put("check", "$kind=$value")
                put("met", met)
            },
            observation = observed,
            postcondition = buildJsonObject {
                put("verified", met)
                put("check", "$kind=$value")
                put("observation_id", observed.observationId)
            },
        )
    }
}

private const val DEFAULT_WAIT_MS = 3_000L
private const val MIN_WAIT_MS = 100L
private const val MAX_WAIT_MS = 30_000L
private const val POLL_INTERVAL_MS = 150L

/**
 * One string argument, read as JSON rather than as text.
 *
 * `JsonElement.toString()` re-serializes - it hands back a quoted
 * `"Search"`, and hands back an escaped quote inside a value intact - so
 * trimming quotes off it only works until an argument contains one.
 * `contentOrNull` is the decoded value, and null for JSON null, which is
 * why a missing argument and an explicitly-null one behave alike here.
 */
internal fun JsonObject.stringArg(name: String): String? =
    (this[name] as? JsonPrimitive)?.contentOrNull
        ?.trim()?.takeIf { it.isNotEmpty() }

internal fun JsonObject.longArg(name: String): Long? =
    (this[name] as? JsonPrimitive)?.contentOrNull?.trim()?.toLongOrNull()

/**
 * Directive arguments as the executor's internal [AgentAction].
 *
 * `AgentAction` survives only as an execution primitive - the wire
 * protocol above it is [ToolCallDirective], and nothing parses model
 * prose to build one anymore. Null means no executable form exists
 * (e.g. press_key with an unsupported key name).
 */
fun ToolCallDirective.toAgentAction(): AgentAction? {
    val args = arguments

    return when (tool) {
        "android.tap" -> AgentAction(
            action = "click",
            nodeId = args.stringArg("node_id") ?: args.stringArg("text")?.takeIf { it.startsWith("node_") },
            text = args.stringArg("text"),
        )
        "android.long_press" -> AgentAction(
            action = "long_click",
            nodeId = args.stringArg("node_id") ?: args.stringArg("text")?.takeIf { it.startsWith("node_") },
            text = args.stringArg("text"),
        )
        "android.swipe" -> AgentAction(
            action = "scroll_screen",
            direction = args.stringArg("direction"),
        )
        "android.type_text" -> AgentAction(
            action = "input_text",
            nodeId = args.stringArg("node_id"),
            text = args.stringArg("text"),
        )
        "android.press_key" -> when (args.stringArg("key")?.lowercase()) {
            "enter", "search", "submit" -> AgentAction(action = "submit")
            "backspace", "delete", "clear" -> AgentAction(
                action = "clear_text",
                nodeId = args.stringArg("node_id"),
            )
            else -> null
        }
        "android.back" -> AgentAction(action = "back")
        "android.home" -> AgentAction(action = "home")
        "android.launch_app" -> {
            val rawPkg = args.stringArg("package") ?: args.stringArg("app")
            val resolvedPkg = when (rawPkg?.trim()?.lowercase()) {
                "aura", "com.aura", "aura companion" -> "com.aura.companion"
                "youtube" -> "com.google.android.youtube"
                "chrome" -> "com.android.chrome"
                "settings" -> "com.android.settings"
                else -> rawPkg
            }
            AgentAction(
                action = "open_app",
                packageName = resolvedPkg,
            )
        }
        else -> null
    }
}
