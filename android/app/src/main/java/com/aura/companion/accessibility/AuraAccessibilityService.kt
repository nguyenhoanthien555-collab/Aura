package com.aura.companion.accessibility

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.aura.companion.AuraApplication
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.AuraResult
import com.aura.companion.data.settings.SettingsStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.jsonObject
import java.util.concurrent.atomic.AtomicReference

class AuraAccessibilityService : AccessibilityService() {

    private lateinit var settings: SettingsStore
    private lateinit var repository: AuraRepository
    private val safetyGuard = SafetyGuard()
    private val executor by lazy { AuraActionExecutor(this) }

    private val job = SupervisorJob()
    private val scope = CoroutineScope(Dispatchers.Main + job)

    private var agentJob: Job? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        val container = (application as AuraApplication).container
        settings = container.settings
        repository = container.repository
        instance.set(this)
        Log.d("AuraAgentService", "Aura Accessibility Service Connected")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Event-driven updates can be handled here if needed.
    }

    override fun onInterrupt() {
        stopAgentLoop()
    }

    override fun onDestroy() {
        super.onDestroy()
        instance.set(null)
        scope.cancel()
    }

    /**
     * Execution result that carries verification context.
     */
    sealed interface ExecutionResult {
        /** Action executed AND verification passed (UI actually changed). */
        object Verified : ExecutionResult
        /** Action was executed but verification could not confirm a UI change.
         *  This is NOT a hard failure — some actions have subtle effects. */
        object Unverified : ExecutionResult
        /** Action execution itself failed (click didn't work, node not found, etc). */
        object Failed : ExecutionResult
        /** Action was blocked by SafetyGuard. */
        object Blocked : ExecutionResult
    }

    /**
     * Lightweight fingerprint of the current UI state for verification.
     * Compares package, node count, and a hash of text content.
     */
    data class ScreenFingerprint(
        val packageName: String,
        val nodeCount: Int,
        val contentHash: Int
    ) {
        companion object {
            fun capture(root: AccessibilityNodeInfo?): ScreenFingerprint? {
                if (root == null) return null
                val pkg = root.packageName?.toString().orEmpty()
                var count = 0
                var hash = 0

                fun walk(node: AccessibilityNodeInfo?) {
                    if (node == null) return
                    count++
                    val text = node.text?.toString().orEmpty()
                    val desc = node.contentDescription?.toString().orEmpty()
                    hash = 31 * hash + text.hashCode()
                    hash = 31 * hash + desc.hashCode()
                    for (i in 0 until node.childCount) {
                        val child = node.getChild(i)
                        if (child != null) {
                            walk(child)
                            child.recycle()
                        }
                    }
                }

                walk(root)
                // Do NOT recycle root — the caller owns it
                return ScreenFingerprint(pkg, count, hash)
            }
        }
    }

    fun startAgentLoop(request: String, onComplete: (String) -> Unit) {
        agentJob?.cancel()
        agentJob = scope.launch {
            var stepCount = 0
            val maxSteps = 10
            var currentRequest = request
            var finalMessage = "Task failed to complete."
            var lastActionError: String? = null
            val failedActionsCount = mutableMapOf<String, Int>()
            var parseFailures = 0

            // Set by every path that ends the loop on purpose. Without
            // it, a task that finished on the final step was reported as
            // a timeout, because `stepCount >= maxSteps` was true for
            // both "ran out of steps" and "used the last one well".
            var settled = false

            while (stepCount < maxSteps) {
                stepCount++
                Log.d("AuraAgent", "Starting agent step $stepCount for task: $currentRequest")

                val root = rootInActiveWindow
                if (root == null) {
                    Log.d("AuraAgent", "No active window root found. Retrying in 1s...")
                    delay(1000)
                    continue
                }

                val activePackage = root.packageName?.toString().orEmpty()
                val nodeMap = mutableMapOf<String, AccessibilityNodeInfo>()
                val tree = AccessibilityNodeSerializer.serialize(root, nodeMap)
                root.recycle()

                val displayMetrics = resources.displayMetrics
                val deviceState = DeviceState(
                    width = displayMetrics.widthPixels,
                    height = displayMetrics.heightPixels
                )

                val appInfo = AppInfo(
                    packageName = activePackage,
                    label = runCatching {
                        packageManager.getApplicationLabel(
                            packageManager.getApplicationInfo(activePackage, 0)
                        ).toString()
                    }.getOrDefault(activePackage)
                )

                val snapshot = AccessibilitySnapshot(
                    device = deviceState,
                    app = appInfo,
                    accessibilityTree = tree,
                    screenshotAvailable = false,
                    userRequest = currentRequest,
                    lastActionError = lastActionError
                )

                // Clear error so we only send it once
                lastActionError = null

                val jsonContext = Json.encodeToJsonElement(snapshot).jsonObject

                Log.d("AuraAgent", "Sending screen snapshot to backend...")
                val result = repository.send(AGENT_TICK, jsonContext)

                // Recycle tree node references immediately after sending request to prevent leaks
                nodeMap.values.forEach { it.recycle() }

                when (result) {
                    is AuraResult.Ok -> {
                        val reply = result.value.reply.trim()
                        Log.d("AuraAgent", "Backend response: $reply")

                        when (val parsed = AgentActionParser.parse(reply)) {

                            is AgentActionParser.ParseResult.Failure -> {
                                // Not fatal. A model that fenced its JSON
                                // or added a field is one correction away
                                // from a usable answer, and the correction
                                // travels on `last_action_error` - the
                                // same channel a failed tap uses. The
                                // outer `stepCount < maxSteps` is what
                                // bounds the retrying.
                                parseFailures++
                                Log.w(
                                    "AuraAgent",
                                    "Unparseable reply ($parseFailures/$MAX_PARSE_FAILURES): ${parsed.reason}"
                                )

                                if (parseFailures >= MAX_PARSE_FAILURES) {
                                    finalMessage =
                                        "Aura could not read the action plan after " +
                                        "$parseFailures attempts, so nothing was done."
                                    settled = true
                                    break
                                }

                                lastActionError = parsed.reason
                            }

                            is AgentActionParser.ParseResult.Success -> {
                                val action = parsed.action

                                // A reply we could read resets the budget:
                                // the allowance is for consecutive
                                // failures, not for the task as a whole.
                                parseFailures = 0

                                if (action.action == "complete") {
                                    finalMessage = action.message ?: "Task completed successfully!"
                                    Log.d("AuraAgent", "Task complete: $finalMessage")
                                    settled = true
                                    break
                                }

                                // Check consecutive failures for this node/action
                                val actionKey = "${action.action}:${action.nodeId}"
                                if ((failedActionsCount[actionKey] ?: 0) >= 2) {
                                    lastActionError = "Action ${action.action} on ${action.nodeId} failed repeatedly (${failedActionsCount[actionKey]} times). Target is not actionable. Try a different approach."
                                    Log.w("AuraAgent", lastActionError!!)
                                    // Reset counter so the LLM has a chance to try a new path
                                    failedActionsCount.remove(actionKey)
                                    continue
                                }

                                when (executeActionWithRecovery(action, tree)) {
                                    is ExecutionResult.Verified -> {
                                        failedActionsCount.remove(actionKey)
                                        delay(500) // Short stabilization after verified success
                                    }
                                    is ExecutionResult.Unverified -> {
                                        // The action executed without error, but we could not confirm UI change.
                                        // Treat as conditional success — let the LLM see the next screen state.
                                        // But track it: if the same action keeps being unverified, it's likely failing.
                                        val count = (failedActionsCount[actionKey] ?: 0) + 1
                                        failedActionsCount[actionKey] = count
                                        if (count >= 2) {
                                            lastActionError = "Action ${action.action} on ${action.nodeId} executed but UI did not change (${count} times). The action may not be working."
                                        }
                                        delay(500)
                                    }
                                    is ExecutionResult.Blocked -> {
                                        finalMessage = "Action blocked for safety: attempted ${action.action} containing sensitive targets."
                                        settled = true
                                        break
                                    }
                                    is ExecutionResult.Failed -> {
                                        failedActionsCount[actionKey] = (failedActionsCount[actionKey] ?: 0) + 1
                                        lastActionError = "Action ${action.action} on ${action.nodeId} failed. Target not clickable or not found."
                                    }
                                }
                            }
                        }
                    }
                    is AuraResult.Failed -> {
                        finalMessage = "Aura server request failed: ${result.error.userMessage}"
                        Log.e("AuraAgent", finalMessage)
                        settled = true
                        break
                    }
                }
            }

            if (!settled && stepCount >= maxSteps) {
                finalMessage = "Task timed out: maximum number of steps reached."
            }

            onComplete(finalMessage)
        }
    }

    private suspend fun executeActionWithRecovery(
        action: AgentAction,
        oldTree: Map<String, AccessibilityNode>
    ): ExecutionResult {
        var attempt = 0
        val maxAttempts = 2

        while (attempt < maxAttempts) {
            attempt++
            Log.d("AuraAgent", "Attempt $attempt to execute ${action.action} for ${action.nodeId}")

            // --- Capture pre-action state ---
            val preRoot = rootInActiveWindow
            val preFingerprint = if (preRoot != null) {
                val fp = ScreenFingerprint.capture(preRoot)
                preRoot.recycle()
                fp
            } else {
                null
            }

            if (preFingerprint == null && action.action != "open_app") {
                Log.d("AuraAgent", "No active window root found during execution. Retrying in 1s...")
                delay(1000)
                continue
            }

            Log.d("AuraAgent", "Pre-action state: package=${preFingerprint?.packageName} nodes=${preFingerprint?.nodeCount} hash=${preFingerprint?.contentHash}")

            // --- Resolve and execute ---
            val execRoot = rootInActiveWindow
            if (execRoot == null && action.action != "open_app") {
                Log.d("AuraAgent", "No active window root for execution. Retrying in 1s...")
                delay(1000)
                continue
            }

            val freshNodeMap = mutableMapOf<String, AccessibilityNodeInfo>()

            // Resolve target node using AuraActionExecutor
            val (resolvedNode, resolvedNodeMeta) = if (action.nodeId != null && execRoot != null) {
                executor.resolveNode(action.nodeId, oldTree, execRoot, freshNodeMap)
            } else {
                Pair(null, null)
            }

            // Log diagnostics for the target node
            if (action.nodeId != null) {
                executor.logNodeDiagnostics("AuraActionExecutor", action.nodeId, resolvedNode, resolvedNodeMeta)
            }

            // Run SafetyGuard check on the resolved node
            if (!safetyGuard.checkAction(action, resolvedNode)) {
                freshNodeMap.values.forEach { it.recycle() }
                execRoot?.recycle()
                Log.w("AuraAgent", "Action blocked for safety: attempted ${action.action} containing sensitive targets.")
                return ExecutionResult.Blocked
            }

            // Execute the action with the resolved node
            val success = executor.executeWithNode(action, resolvedNode, resolvedNodeMeta, freshNodeMap)

            // Recycle all fresh node references to prevent leaks
            freshNodeMap.values.forEach { it.recycle() }
            execRoot?.recycle()

            if (success) {
                // --- Verify action: capture post-action state ---
                delay(1200) // Wait for UI to settle

                val postRoot = rootInActiveWindow
                val postFingerprint = if (postRoot != null) {
                    val fp = ScreenFingerprint.capture(postRoot)
                    postRoot.recycle()
                    fp
                } else {
                    null
                }

                Log.d("AuraAgent", "Post-action state: package=${postFingerprint?.packageName} nodes=${postFingerprint?.nodeCount} hash=${postFingerprint?.contentHash}")

                // Determine verification result
                val verified = verifyStateChange(action, preFingerprint, postFingerprint)
                Log.d("AuraAgent", "Action verification result=${if (verified) "VERIFIED" else "UNVERIFIED"} for ${action.action} on ${action.nodeId}")

                return if (verified) ExecutionResult.Verified else ExecutionResult.Unverified
            } else {
                Log.w("AuraAgent", "Attempt $attempt failed for action ${action.action}")
                if (attempt < maxAttempts) {
                    delay(1000)
                }
            }
        }
        return ExecutionResult.Failed
    }

    /**
     * Verifies that an action actually changed the UI state.
     *
     * Different action types have different verification criteria:
     * - open_app: the foreground package must BE the requested package
     * - click: either package changed, node count changed, or content changed
     * - input_text: content hash should change
     * - scroll: node count or content hash should change
     * - back/home/global actions: package or content change expected
     */
    private fun verifyStateChange(
        action: AgentAction,
        pre: ScreenFingerprint?,
        post: ScreenFingerprint?
    ): Boolean {
        // If we couldn't capture either fingerprint, we can't verify — assume cautious unverified
        if (pre == null || post == null) return false

        return when (action.action) {
            "open_app" -> verifyOpenApp(action.packageName.orEmpty(), pre, post)
            "click" -> {
                // For clicks, any meaningful state change counts
                val packageChanged = post.packageName != pre.packageName
                val nodesChanged = post.nodeCount != pre.nodeCount
                val contentChanged = post.contentHash != pre.contentHash

                val changed = packageChanged || nodesChanged || contentChanged
                if (!changed) {
                    Log.w("AuraAgent", "click verification UNVERIFIED: package=${pre.packageName}->${post.packageName}, nodes=${pre.nodeCount}->${post.nodeCount}, hash=${pre.contentHash}->${post.contentHash}")
                } else {
                    Log.d("AuraAgent", "click verification detail: pkgChanged=$packageChanged nodesChanged=$nodesChanged contentChanged=$contentChanged")
                }
                changed
            }
            "input_text", "clear_text" -> {
                // Text input should change content
                post.contentHash != pre.contentHash
            }
            "scroll", "scroll_screen" -> {
                // Scroll should change visible content
                post.contentHash != pre.contentHash || post.nodeCount != pre.nodeCount
            }
            "back", "home" -> {
                // These should change something
                post.packageName != pre.packageName || post.contentHash != pre.contentHash
            }
            else -> {
                // For other actions, any change counts
                post.packageName != pre.packageName ||
                        post.nodeCount != pre.nodeCount ||
                        post.contentHash != pre.contentHash
            }
        }
    }

    fun stopAgentLoop() {
        agentJob?.cancel()
        agentJob = null
    }

    companion object {
        private val instance = AtomicReference<AuraAccessibilityService?>(null)

        /**
         * The message body of an agent step.
         *
         * A label for the log line, not a contract: the server decides a
         * turn is an agent step from the context object carrying the
         * screen, never from this string (see brain/agent_mode.py).
         */
        const val AGENT_TICK = "agent_tick"

        /**
         * Consecutive replies we cannot read before giving up.
         *
         * Above one, because a fenced or prose-wrapped reply usually
         * becomes valid JSON once the model is told what was wrong. Well
         * below `maxSteps`, so a model that has genuinely lost the format
         * does not spend the whole step budget failing the same way, and
         * the user hears about it while it is still their request.
         */
        const val MAX_PARSE_FAILURES = 3

        /**
         * Did `open_app` actually put [target] in the foreground?
         *
         * When the target is known this is an identity check and nothing
         * weaker. The previous rule accepted *any* package change, which
         * made a launcher redirect, a permission dialog, a chooser sheet
         * or a crash back to the home screen all count as "YouTube is
         * open" - the agent then reported success for an app that was
         * never opened, which is the one thing this loop must never do
         * (AURA-P1-003). Being wrong here is worse than being unsure:
         * an `Unverified` result costs one more screen for the model to
         * look at, while a false `Verified` ends the task with a lie.
         *
         * With no target named, an identity check has nothing to compare
         * against, so a package change is the strongest evidence
         * available and is accepted as such.
         *
         * Sits in the companion object because the enclosing class is an
         * [AccessibilityService] and cannot be constructed in a JVM unit
         * test, and a verification rule that decides whether Aura claims
         * success is worth testing directly.
         */
        fun verifyOpenApp(
            target: String,
            pre: ScreenFingerprint,
            post: ScreenFingerprint
        ): Boolean {

            if (target.isBlank()) {

                val changed = post.packageName != pre.packageName

                if (!changed) {
                    Log.w(
                        "AuraAgent",
                        "open_app verification FAILED: no package named and " +
                            "foreground unchanged (${pre.packageName})"
                    )
                }

                return changed
            }

            val arrived = post.packageName == target

            if (!arrived) {
                Log.w(
                    "AuraAgent",
                    "open_app verification FAILED: expected package=$target, " +
                        "got=${post.packageName} (was ${pre.packageName})"
                )
            }

            return arrived
        }

        fun isEnabled(): Boolean = instance.get() != null

        fun startAgentTask(request: String, onComplete: (String) -> Unit): Boolean {
            val service = instance.get()
            return if (service != null) {
                service.startAgentLoop(request, onComplete)
                true
            } else {
                false
            }
        }

        fun stopAgentTask() {
            instance.get()?.stopAgentLoop()
        }
    }
}
