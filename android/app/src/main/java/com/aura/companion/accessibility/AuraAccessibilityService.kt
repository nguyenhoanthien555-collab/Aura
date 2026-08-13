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
import com.aura.companion.screen.AccessibilityScreenshotCapture
import com.aura.companion.screen.ScreenshotOutcome
import com.aura.companion.screen.ScreenshotUploader
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
    private lateinit var screenshots: ScreenshotUploader
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
        screenshots = ScreenshotUploader(
            capture = AccessibilityScreenshotCapture(this),
            repository = repository,
            settings = settings,
            cacheDir = cacheDir,
        )
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
            val completedActions = mutableListOf<String>()
            var lastVerifiedAction: AgentAction? = null

            // Set by every path that ends the loop on purpose. Without
            // it, a task that finished on the final step was reported as
            // a timeout, because `stepCount >= maxSteps` was true for
            // both "ran out of steps" and "used the last one well".
            var settled = false
            var skipScreenshot = false


            while (stepCount < maxSteps) {
                stepCount++
                val stepStartMs = System.currentTimeMillis()
                Log.d("AuraAgent", "Starting agent step $stepCount for task: $currentRequest")

                val t0 = System.currentTimeMillis()
                val root = rootInActiveWindow
                if (root == null) {
                    Log.d("AuraAgent", "No active window root found. Retrying in 300ms...")
                    delay(300)
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
                val snapshotMs = System.currentTimeMillis() - t0

                val screenshot = if (skipScreenshot) {
                    ScreenshotOutcome.Skipped("disabled for loop")
                } else {
                    screenshots.upload(
                        application = appInfo.label ?: activePackage,
                        packageName = activePackage,
                    )
                }

                if (screenshot is ScreenshotOutcome.Failed) {
                    Log.w("AuraAgent", "Screenshot not delivered: ${screenshot.reason}")
                    if (screenshot.reason.contains("503") || screenshot.reason.contains("screen_disabled") || screenshot.reason.contains("waking up")) {
                        skipScreenshot = true
                    }
                }

                val snapshot = AccessibilitySnapshot(
                    device = deviceState,
                    app = appInfo,
                    accessibilityTree = tree,
                    screenshotAvailable = screenshot is ScreenshotOutcome.Sent,
                    userRequest = currentRequest,
                    lastActionError = lastActionError,
                    completedActions = completedActions.toList()
                )

                // Clear error so we only send it once
                lastActionError = null

                val jsonContext = Json.encodeToJsonElement(snapshot).jsonObject

                Log.d("AuraAgent", "Sending screen snapshot to backend...")
                val netStartMs = System.currentTimeMillis()
                val result = repository.send(AGENT_TICK, jsonContext)
                val networkMs = System.currentTimeMillis() - netStartMs

                // Recycle tree node references immediately after sending request to prevent leaks
                nodeMap.values.forEach { it.recycle() }

                when (result) {
                    is AuraResult.Ok -> {
                        val reply = result.value.reply.trim()
                        Log.d("AuraAgent", "Backend response: $reply")

                        when (val parsed = AgentActionParser.parse(reply)) {

                            is AgentActionParser.ParseResult.Failure -> {
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

                                parseFailures = 0

                                if (action.action == "complete") {
                                    finalMessage = action.message ?: "Task completed successfully!"
                                    Log.d("AuraAgent", "Task complete: $finalMessage")
                                    settled = true
                                    break
                                }

                                // Deterministic repeated-action guard
                                if (isRepeatedVerifiedAction(action, lastVerifiedAction, activePackage)) {
                                    lastActionError = "This action (${action.action}) was already successfully executed. Do not repeat it. Continue with the next unfinished part of the user's task."
                                    Log.w("AuraAgent", lastActionError!!)
                                    continue
                                }

                                // Check consecutive failures for this node/action
                                val actionKey = "${action.action}:${action.nodeId}"
                                if ((failedActionsCount[actionKey] ?: 0) >= 2) {
                                    lastActionError = "Action ${action.action} on ${action.nodeId} failed repeatedly (${failedActionsCount[actionKey]} times). Target is not actionable. Try a different approach."
                                    Log.w("AuraAgent", lastActionError!!)
                                    failedActionsCount.remove(actionKey)
                                    continue
                                }

                                val execStartMs = System.currentTimeMillis()
                                val execResult = executeActionWithRecovery(action, tree)
                                val execVerifyMs = System.currentTimeMillis() - execStartMs
                                val stepTotalMs = System.currentTimeMillis() - stepStartMs
                                Log.d("AuraAgent", "PERF step=$stepCount snapshot=${snapshotMs}ms network=${networkMs}ms execVerify=${execVerifyMs}ms total=${stepTotalMs}ms")

                                when (execResult) {
                                    is ExecutionResult.Verified -> {
                                        failedActionsCount.remove(actionKey)
                                        lastVerifiedAction = action
                                        completedActions.add(formatActionHistory(action))
                                        lastActionError = null

                                        if (shouldAutoComplete(currentRequest, action) || isSearchTaskComplete(currentRequest, action, completedActions) || isSelectionTaskComplete(currentRequest, action, completedActions)) {
                                            finalMessage = action.message ?: completionMessageForAction(action)
                                            Log.d("AuraAgent", "Task completed: $finalMessage")
                                            settled = true
                                            break
                                        }

                                        delay(150) // Reduced stabilization after verified success
                                    }

                                    is ExecutionResult.Unverified -> {
                                        val count = (failedActionsCount[actionKey] ?: 0) + 1
                                        failedActionsCount[actionKey] = count
                                        if (count >= 2) {
                                            lastActionError = "Action ${action.action} on ${action.nodeId} executed but UI did not change (${count} times). The action may not be working."
                                        }
                                        delay(150)
                                    }
                                    is ExecutionResult.Blocked -> {

                                        finalMessage = "Action blocked for safety: attempted ${action.action} containing sensitive targets."
                                        settled = true
                                        break
                                    }
                                    is ExecutionResult.Failed -> {
                                        failedActionsCount[actionKey] = (failedActionsCount[actionKey] ?: 0) + 1
                                        lastActionError = failureReason(action)
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

            // Launching an app that is ALREADY the foreground package is a
            // visible duplicate launch. The agent loop also re-issues
            // open_app on the next step whenever verification is slow, so
            // without this a launch that took a moment to draw would be
            // re-requested while it was still coming up. Succeed without
            // calling startActivity again: the foreground check IS the
            // verification here, there is nothing to wait for.
            if (action.action == "open_app") {
                val target = action.packageName?.trim().orEmpty()
                if (target.isNotEmpty() && preFingerprint?.packageName == target) {
                    freshNodeMap.values.forEach { it.recycle() }
                    execRoot?.recycle()
                    Log.d("AuraAgent", "open_app verification VERIFIED: expected=$target (already foreground, no relaunch)")
                    return ExecutionResult.Verified
                }
            }

            // Execute the action with the resolved node
            val success = executor.executeWithNode(action, resolvedNode, resolvedNodeMeta, freshNodeMap)

            // Recycle all fresh node references to prevent leaks
            freshNodeMap.values.forEach { it.recycle() }
            execRoot?.recycle()

            if (success) {
                // --- Verify action ---
                val verified = verifyActionOutcome(action, preFingerprint)
                Log.d("AuraAgent", "Action verification result=${if (verified) "VERIFIED" else "UNVERIFIED"} for ${action.action} on ${action.nodeId}")

                return if (verified) ExecutionResult.Verified else ExecutionResult.Unverified
            } else {
                Log.w("AuraAgent", "Attempt $attempt failed for action ${action.action}")
                if (attempt < maxAttempts) {
                    delay(300)
                }
            }

        }
        return ExecutionResult.Failed
    }

    /**
     * Verify that [action] actually changed the UI, with the settle
     * semantics the action needs.
     *
     * open_app is verified *eventually*: `startActivity` returns the moment
     * the launch is accepted, while the accessibility window can keep
     * reporting the previous app until the target activity draws - YouTube
     * took ~900ms in the field, longer than the generic 250ms settle. A
     * single immediate snapshot read "expected YouTube, got Aura", returned
     * UNVERIFIED, and the loop re-issued the same open_app on the next step
     * (a duplicate launch for an app that was already coming up). So
     * open_app polls the foreground package until it IS the target, within
     * a bounded budget; see `waitForForegroundPackage`.
     *
     * Every other action keeps the original single settle delay + one
     * snapshot comparison, unchanged.
     */
    private suspend fun verifyActionOutcome(
        action: AgentAction,
        pre: ScreenFingerprint?,
    ): Boolean {
        if (action.action == "open_app") {
            val target = action.packageName?.trim().orEmpty()
            if (target.isNotEmpty()) {
                return waitForForegroundPackage(target) {
                    val root = rootInActiveWindow
                    val pkg = root?.packageName?.toString()
                    root?.recycle()
                    pkg
                }
            }
            // No target named: fall through to the generic settle + change
            // check, which accepts any package change (verifyOpenApp's
            // no-target branch).
        }

        // --- Generic path: wait for UI to settle, then one snapshot ---
        delay(250)

        val postRoot = rootInActiveWindow
        val postFingerprint = if (postRoot != null) {
            val fp = ScreenFingerprint.capture(postRoot)
            postRoot.recycle()
            fp
        } else {
            null
        }

        Log.d("AuraAgent", "Post-action state: package=${postFingerprint?.packageName} nodes=${postFingerprint?.nodeCount} hash=${postFingerprint?.contentHash}")

        return verifyStateChange(action, pre, postFingerprint)
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
         * Why an action failed, written for the model.
         *
         * Goes out as `last_action_error`, which is the only channel the
         * model has to correct itself, so it has to say something the
         * model can act on. The single generic sentence this replaced -
         * "Target not clickable or not found" - was false for `open_app`
         * in two ways at once: there is no target node to be unclickable
         * (`node_id` is null, so the message read "on null"), and it
         * named neither the package that was tried nor the reason it did
         * not launch. A model told that cannot do anything but guess
         * again, which is how a single wrong package name consumed every
         * remaining step.
         *
         * Every other action does reference a node from the tree the
         * model was just shown, so for those the original sentence is
         * accurate and is kept verbatim.
         */
        fun failureReason(action: AgentAction): String {

            if (action.action != "open_app") {
                return "Action ${action.action} on ${action.nodeId} failed. " +
                    "Target not clickable or not found."
            }

            val target = action.packageName?.trim().orEmpty()

            if (target.isEmpty()) {
                return "open_app needs a \"package\" field holding an Android " +
                    "package name and none was sent. Send the package name, " +
                    "for example \"com.google.android.youtube\"."
            }

            return "\"$target\" could not be launched: no installed app has " +
                "that package name. Send the app's exact Android package " +
                "name - the app's visible label is not a package name - or " +
                "reach the app another way, such as \"home\" and then a tap."
        }

        /**
         * How long open_app may take to become the foreground package
         * after `startActivity` returns, before verification gives up.
         *
         * Chosen from observed behaviour, not a guess: the target activity
         * draws after the launch is accepted (YouTube measured ~900ms in
         * the field), and a cold start can take longer. 2500ms is well
         * past a normal draw while staying well inside one agent step's
         * budget, and it is bounded - verification never waits forever.
         */
        const val OPEN_APP_SETTLE_TIMEOUT_MS = 2500L

        /**
         * How often the foreground package is re-sampled while waiting.
         * 150ms keeps the wait responsive and the log readable (a full
         * timeout is ~16 lines) without hammering the accessibility
         * service.
         */
        const val OPEN_APP_SETTLE_INTERVAL_MS = 150L

        /**
         * Bounded polling until the active window reports [target].
         *
         * Returns success only when the target package is actually the
         * foreground/accessibility package - never merely because
         * `startActivity` did not throw. A transient "still the previous
         * app" sample is waited through rather than reported as failure,
         * so the agent loop is not sent back to re-issue the same launch.
         *
         * The clock and the sleep are injected (defaulting to the real
         * clock and `delay`) so a JVM unit test can drive the timing
         * deterministically; the service supplies the package source.
         * Sits in the companion for the same reason `verifyOpenApp` does -
         * the enclosing class is an AccessibilityService and cannot be
         * constructed in a unit test.
         */
        suspend fun waitForForegroundPackage(
            target: String,
            timeoutMs: Long = OPEN_APP_SETTLE_TIMEOUT_MS,
            intervalMs: Long = OPEN_APP_SETTLE_INTERVAL_MS,
            now: () -> Long = System::currentTimeMillis,
            sleep: suspend (Long) -> Unit = { delay(it) },
            currentPackage: () -> String?,
        ): Boolean {
            if (target.isBlank()) return false

            val start = now()
            var attempt = 0
            var lastPackage: String? = null

            while (now() - start < timeoutMs) {
                attempt++
                lastPackage = currentPackage()
                val elapsed = now() - start
                val current = lastPackage.orEmpty()

                if (current == target) {
                    Log.d(
                        "AuraAgent",
                        "open_app foreground verification VERIFIED: expected=$target " +
                            "current=$current elapsed=${elapsed}ms"
                    )
                    return true
                }

                Log.d(
                    "AuraAgent",
                    "open_app verification poll: expected=$target " +
                        "current=${current.ifEmpty { "<none>" }} " +
                        "attempt=$attempt elapsed=${elapsed}ms"
                )

                sleep(intervalMs)
            }

            Log.w(
                "AuraAgent",
                "open_app foreground verification TIMEOUT: expected=$target " +
                    "current=${lastPackage.orEmpty().ifEmpty { "<none>" }} " +
                    "elapsed=${now() - start}ms"
            )
            return false
        }

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

        fun shouldAutoComplete(request: String, action: AgentAction): Boolean {
            val act = action.action
            if (act != "open_app" && act != "home" && act != "open_notifications" && act != "open_quick_settings") {
                return false
            }

            val reqLower = request.lowercase().trim()
            val multiStepKeywords = listOf(
                " và ", " rồi ", " sau đó ", " tiếp ",
                " and ", " then ", " after ", " to ",
                ",", ";"
            )

            return multiStepKeywords.none { keyword -> reqLower.contains(keyword) }
        }

        fun completionMessageForAction(action: AgentAction): String {
            return when (action.action) {
                "open_app" -> "App ${action.packageName ?: ""} launched successfully!"
                "home" -> "Navigated to home screen."
                "open_notifications" -> "Opened notifications."
                "open_quick_settings" -> "Opened quick settings."
                else -> "Task completed successfully!"
            }
        }

        fun formatActionHistory(action: AgentAction): String {
            return when (action.action) {
                "open_app" -> "open_app(${action.packageName.orEmpty()}) [VERIFIED]"
                "home" -> "home() [VERIFIED]"
                "back" -> "back() [VERIFIED]"
                "click" -> "click(${action.nodeId.orEmpty()}) [VERIFIED]"
                "input_text" -> "input_text(${action.nodeId.orEmpty()}, \"${action.text.orEmpty()}\") [VERIFIED]"
                else -> "${action.action}() [VERIFIED]"
            }
        }

        fun isSearchTaskComplete(request: String, action: AgentAction, completedActions: List<String>): Boolean {
            val reqLower = request.lowercase().trim()
            val isSearchReq = reqLower.contains("search") || reqLower.contains("tìm")
            if (!isSearchReq) return false

            val requestsSelection = listOf(
                "play", "select", "pick", "open first", "first song", "first result", "first video",
                "phát", "chơi", "chọn", "nghe", "mở bài", "bài hát đầu tiên", "kết quả đầu tiên"
            ).any { reqLower.contains(it) }

            if (requestsSelection) {
                return false
            }

            val isSubmitted = action.action == "submit" || (action.action == "input_text" && completedActions.any { it.contains("[VERIFIED]") && it.startsWith("input_text") })
            val hasSubmittedHistory = completedActions.any { it.contains("[VERIFIED]") && (it.startsWith("submit") || it.startsWith("input_text")) }

            return isSubmitted || hasSubmittedHistory
        }

        fun isSelectionTaskComplete(request: String, action: AgentAction, completedActions: List<String>): Boolean {
            val reqLower = request.lowercase().trim()
            val requestsSelection = listOf(
                "play", "select", "pick", "open first", "first song", "first result", "first video",
                "phát", "chơi", "chọn", "nghe", "mở bài", "bài hát đầu tiên", "kết quả đầu tiên"
            ).any { reqLower.contains(it) }

            if (!requestsSelection) return false

            val hasSubmittedSearch = completedActions.any { it.contains("[VERIFIED]") && (it.startsWith("submit") || it.startsWith("input_text")) }
            val isClick = action.action == "click"

            return isClick && hasSubmittedSearch
        }

        fun isRepeatedVerifiedAction(
            action: AgentAction,
            lastVerifiedAction: AgentAction?,
            currentPackage: String
        ): Boolean {
            if (lastVerifiedAction == null) return false
            if (action.action != lastVerifiedAction.action) return false

            return when (action.action) {
                "open_app" -> {
                    val targetPkg = action.packageName?.trim().orEmpty()
                    targetPkg.isNotEmpty() && (targetPkg == lastVerifiedAction.packageName?.trim() || currentPackage == targetPkg)
                }
                "home", "open_notifications", "open_quick_settings" -> true
                "click", "long_click", "clear_text" -> {
                    action.nodeId != null && action.nodeId == lastVerifiedAction.nodeId
                }
                "input_text" -> {
                    val text1 = AuraActionExecutor.sanitizeSearchQuery(action.text)
                    val text2 = AuraActionExecutor.sanitizeSearchQuery(lastVerifiedAction.text)
                    text1.isNotEmpty() && text1 == text2
                }
                else -> false
            }
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
