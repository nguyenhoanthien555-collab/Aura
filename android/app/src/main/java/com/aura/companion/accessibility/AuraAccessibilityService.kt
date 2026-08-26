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
import kotlinx.coroutines.CancellationException
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

    /**
     * The screen the last window-state change named, and the package it
     * belonged to.
     *
     * Written on the main thread by [onAccessibilityEvent] and read from
     * the snapshot loop, so a single reference swap rather than two
     * fields: a torn read would pair one app's package with another's
     * activity, which is the one answer worse than no answer.
     */
    private val lastWindow = AtomicReference<Window?>(null)

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

        // A window-state change is the only event that names the screen,
        // and until this read it nothing did: `AppInfo.activity` was
        // declared, serialized and never assigned, so `focus.screen`
        // arrived at the server empty on every tick of every session.
        // Both `brain/task_graph.py` and `brain/recovery.py` say so in as
        // many words and restrict what they are willing to verify because
        // of it, and `tests/test_machine_turns.py` already asserts the
        // server handles a real activity - against a payload the device
        // had no path to sending.
        //
        // Content changes, clicks, focus and scroll events are subscribed
        // to for the agent loop and are deliberately ignored here. They
        // fire constantly and none of them means "the screen is now a
        // different screen", so recording them would replace a stable
        // identity with noise.
        if (event?.eventType != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            return
        }

        val window = windowFrom(
            event.packageName?.toString(),
            event.className?.toString(),
        ) ?: return

        lastWindow.set(window)
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
            // The chat UI keeps its loading flag set until this
            // callback fires, so the loop must deliver it exactly once
            // on EVERY exit - a settled completion, the step budget, a
            // crash, or a cancellation from stopAgentLoop / a newer task
            // / service teardown. `runWithGuaranteedCompletion` is that
            // guarantee: without it, a cancelled or crashed loop left
            // the spinner spinning forever and lost the final message.
            runWithGuaranteedCompletion(
                block = { runAgentSteps(request) },
                onComplete = onComplete,
            )
        }
    }

    /**
     * The agent loop, as one suspend step-until-done.
     *
     * Runs at most `maxSteps` steps: capture the screen, ask the
     * backend for the next action, execute it, verify it, and repeat
     * until the task settles, the step budget is spent, or the
     * backend reports failure. Returns the message to deliver to the
     * caller; it never calls `onComplete` itself - the delivery
     * guarantee lives in `runWithGuaranteedCompletion` so that a
     * cancelled or crashed run still clears the UI.
     */
    private suspend fun runAgentSteps(request: String): String {
        var stepCount = 0
        val maxSteps = 10
        var currentRequest = request
        var finalMessage = "Task failed to complete."
        var lastActionError: String? = null
        val failedActionsCount = mutableMapOf<String, Int>()

        // The same failures, formatted for the wire, keyed the same way so
        // the two cannot disagree about which action they describe. One
        // entry per distinct action rather than one per attempt: the count
        // is on the line, because every tick re-sends the whole list.
        val failedActionLines = mutableMapOf<String, String>()
        var parseFailures = 0
        val completedActions = mutableListOf<String>()
        var lastVerifiedAction: AgentAction? = null

        // Set by every path that ends the loop on purpose. Without
        // it, a task that finished on the final step was reported as
        // a timeout, because `stepCount >= maxSteps` was true for
        // both "ran out of steps" and "used the last one well".
        var settled = false
        var skipScreenshot = false

    // The foreground package the previous tick reported. A change between
    // ticks means a screen transition happened under us - an open_app or
    // click we just verified - and the accessibility tree of the *new*
    // window is often still inflating when the package already reports as
    // the target. One bounded settle per tick (see SNAPSHOT_SETTLE_MS)
    // keeps every reasoning step on a fresh observable state.
    var lastTickPackage = ""


        while (stepCount < maxSteps) {
            stepCount++
            val stepStartMs = System.currentTimeMillis()
            Log.d("AuraAgent", "Starting agent step $stepCount for task: $currentRequest")

            val t0 = System.currentTimeMillis()
            var root = rootInActiveWindow
            if (root == null) {
                Log.d("AuraAgent", "No active window root found. Retrying in 300ms...")
                delay(300)
                continue
            }

            var activePackage = root.packageName?.toString().orEmpty()

            // A tick that follows a verified transition reads the NEW
            // screen. Its package can report before the tree has finished
            // inflating, so give it one bounded settle and re-capture -
            // reasoning on a half-drawn screen is how multi-step tasks
            // burned their step budget flailing at stale snapshots.
            if (lastTickPackage.isNotEmpty() && activePackage != lastTickPackage) {
                Log.d(
                    "AuraAgent",
                    "Foreground changed $lastTickPackage -> $activePackage; settling before snapshot"
                )
                root.recycle()
                delay(SNAPSHOT_SETTLE_MS)
                root = rootInActiveWindow
                if (root == null) {
                    Log.d("AuraAgent", "No active window root after settle. Retrying in 300ms...")
                    delay(300)
                    continue
                }
                activePackage = root.packageName?.toString().orEmpty()
            }
            lastTickPackage = activePackage
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
                activity = activityFor(lastWindow.get(), activePackage),
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
                completedActions = completedActions.toList(),
                failedActions = failedActionLines.values.toList()
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

                            // Check consecutive failures for this action.
                            // Keyed by `actionTarget`, not `nodeId`: a
                            // launch has no node, so this used to be
                            // "open_app:null" for every app and one app's
                            // failures refused another app's first try.
                            val actionKey = actionKey(action)
                            val actionTarget = actionTarget(action)
                            if ((failedActionsCount[actionKey] ?: 0) >= MAX_ACTION_ATTEMPTS) {
                                lastActionError = "Action ${action.action} on ${actionTarget.ifEmpty { "this screen" }} failed repeatedly (${failedActionsCount[actionKey]} times). Target is not actionable. Try a different approach."
                                Log.w("AuraAgent", lastActionError!!)
                                failedActionsCount.remove(actionKey)
                                failedActionLines.remove(actionKey)
                                continue
                            }

                            val execStartMs = System.currentTimeMillis()
                            val execResult = executeActionWithRecovery(action, tree)
                            val execVerifyMs = System.currentTimeMillis() - execStartMs
                            val stepTotalMs = System.currentTimeMillis() - stepStartMs
                            Log.d("AuraAgent", "PERF step=$stepCount snapshot=${snapshotMs}ms network=${networkMs}ms execVerify=${execVerifyMs}ms total=${stepTotalMs}ms")

                            when (execResult) {
                                is ExecutionResult.Verified -> {
                                    // Both, together. Leaving the line
                                    // behind would keep reporting a failure
                                    // the server would count against work
                                    // that has since worked.
                                    failedActionsCount.remove(actionKey)
                                    failedActionLines.remove(actionKey)
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
                                    failedActionLines[actionKey] =
                                        formatActionFailure(action, "UNVERIFIED", count)
                                    if (count >= MAX_ACTION_ATTEMPTS) {
                                        lastActionError = "Action ${action.action} on ${actionTarget.ifEmpty { "this screen" }} executed but UI did not change (${count} times). The action may not be working."
                                    }
                                    delay(150)
                                }
                                is ExecutionResult.Blocked -> {
                                    // Not reported as a failure. The loop
                                    // ends here so no further tick is sent,
                                    // and a safety refusal is not a thing to
                                    // retry - which is exactly what putting
                                    // it on `failed_actions` would invite.
                                    finalMessage = "Action blocked for safety: attempted ${action.action} containing sensitive targets."
                                    settled = true
                                    break
                                }
                                is ExecutionResult.Failed -> {
                                    val count = (failedActionsCount[actionKey] ?: 0) + 1
                                    failedActionsCount[actionKey] = count
                                    failedActionLines[actionKey] =
                                        formatActionFailure(action, "FAILED", count)
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

        return finalMessage
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
     * submit is verified eventually for the same reason one action later
     * in the same flow: the IME action returns immediately and the results
     * arrive over the network, so the 250ms snapshot reads the unchanged
     * query screen and a working submit is reported UNVERIFIED. It polls
     * for the screen to move; see `waitForContentChange`.
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

        if (action.action == "submit" && pre != null) {
            return waitForContentChange(pre) {
                val root = rootInActiveWindow
                val fp = ScreenFingerprint.capture(root)
                root?.recycle()
                fp
            }
            // pre == null falls through to the generic path, which returns
            // false without a baseline - the same verdict this would give,
            // reached without pretending to compare against nothing.
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

    /**
     * A window the accessibility layer has named, and the app it is in.
     *
     * Both halves travel together because neither is usable alone: the
     * class identifies the screen, and the package is what later tells us
     * whether that screen still belongs to the app in front of the owner.
     */
    data class Window(val packageName: String, val className: String)

    companion object {
        private val instance = AtomicReference<AuraAccessibilityService?>(null)

        /**
         * A window worth remembering, or null to keep the last one.
         *
         * Android hands both fields as nullable CharSequences and either
         * can be absent on a real event. A blank either way is not a
         * screen, and must not overwrite a screen we already know:
         * blanking it would turn "no news" into "nothing there", and
         * those mean opposite things to `CognitiveState.observe` - the
         * first keeps the last known screen, the second asserts an empty
         * one.
         *
         * Sits in the companion object for the same reason
         * [verifyOpenApp] does: the enclosing class is an
         * [AccessibilityService] and cannot be constructed in a JVM unit
         * test, and a rule that decides what Aura tells the server is on
         * screen is worth testing directly.
         */
        fun windowFrom(packageName: String?, className: String?): Window? {

            val pkg = packageName?.trim().orEmpty()
            val cls = className?.trim().orEmpty()

            if (pkg.isEmpty() || cls.isEmpty()) {
                return null
            }

            return Window(pkg, cls)
        }

        /**
         * The screen to report for the app currently in the foreground.
         *
         * A remembered window belongs to the package it was seen in. When
         * the foreground package has moved on and no window-state change
         * has named the new app's screen yet, reporting the remembered one
         * would pair this app's package with that app's activity - a
         * sentence about the device that was never true at any instant.
         *
         * Null is the honest answer and it costs almost nothing: the
         * server reads a missing screen as "no news" and keeps the one it
         * had, which is the same thing it does for a screen still
         * loading. So the cost of the race is one tick of a slightly old
         * screen, against a permanent risk of naming the wrong app's.
         */
        fun activityFor(window: Window?, foregroundPackage: String): String? {

            if (window == null) {
                return null
            }

            if (window.packageName != foregroundPackage.trim()) {
                return null
            }

            return window.className
        }

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
         * Attempts one action gets before the loop stops executing it.
         *
         * The floor the server's retry policy is pinned to. This number
         * *acts* - past it the service refuses to perform the action at
         * all - so a server bound above it would be permission the phone
         * declines to honour, while one below it is enforceable because
         * the server simply stops asking. `brain/recovery.py` holds the
         * policy and `tests/test_agent_protocol.py` reads this constant,
         * so the two cannot part company unnoticed.
         *
         * Two, unchanged in value: this replaces a bare literal that
         * appeared three times in the loop.
         */
        const val MAX_ACTION_ATTEMPTS = 2

        /**
         * How long the loop waits after noticing the foreground package
         * changed, before snapshotting for the next reasoning step.
         *
         * Shorter than `OPEN_APP_SETTLE_TIMEOUT_MS` on purpose: that wait
         * has to prove a launch worked, this one only gives a window that
         * already reported its package a moment to finish drawing its
         * tree. 600ms covers the observed draw time of a warm activity
         * without making ten-step tasks pay a full second per transition.
         * Bounded by definition - one wait per tick, never a loop.
         */
        const val SNAPSHOT_SETTLE_MS = 600L

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
         * How long a submitted query is given to produce results.
         *
         * Longer than the open_app budget on purpose: open_app waits for
         * a local activity to draw, while submit waits for a network
         * round trip plus a draw. 3000ms is past a normal search on a
         * working connection and still leaves nine of ten steps intact,
         * and the poll exits the moment the screen moves - a fast search
         * pays one interval, not the budget.
         */
        const val SUBMIT_SETTLE_TIMEOUT_MS = 3000L

        /** Re-sample cadence while waiting for results; see above. */
        const val SUBMIT_SETTLE_INTERVAL_MS = 150L

        /**
         * Bounded polling until the screen differs from [pre].
         *
         * The submit counterpart to [waitForForegroundPackage], and it
         * exists for the same reason: `submit` fires the IME action and
         * returns immediately, while the results it asked for arrive over
         * the network. A single snapshot 250ms later is still the
         * unchanged query screen, so a submit that worked was reported
         * UNVERIFIED and the agent spent a step re-submitting a search
         * already in flight.
         *
         * Success is any of the three fingerprint fields moving. That is
         * a deliberately low bar for evidence, and it is still evidence -
         * far more than "the IME action did not throw", which is what
         * section 11 forbids relying on. What it cannot do is confirm the
         * results are *relevant*; that is the caller's job, and on the
         * search path the result-selection heuristics already do it.
         *
         * A null sample is waited through rather than failed:
         * `rootInActiveWindow` is null exactly during the window
         * transition a working submit causes, and failing on it would
         * reintroduce the bug through another door.
         *
         * Clock and sleep are injected (defaulting to the real clock and
         * `delay`) so a JVM unit test drives the timing deterministically;
         * the service supplies the fingerprint source.
         */
        suspend fun waitForContentChange(
            pre: ScreenFingerprint,
            label: String = "submit",
            timeoutMs: Long = SUBMIT_SETTLE_TIMEOUT_MS,
            intervalMs: Long = SUBMIT_SETTLE_INTERVAL_MS,
            now: () -> Long = System::currentTimeMillis,
            sleep: suspend (Long) -> Unit = { delay(it) },
            currentFingerprint: () -> ScreenFingerprint?,
        ): Boolean {
            val start = now()
            var attempt = 0

            while (now() - start < timeoutMs) {
                attempt++
                val post = currentFingerprint()
                val elapsed = now() - start

                if (post != null && post != pre) {
                    Log.d(
                        "AuraAgent",
                        "$label content verification VERIFIED: " +
                            "package=${pre.packageName}->${post.packageName} " +
                            "nodes=${pre.nodeCount}->${post.nodeCount} " +
                            "hash=${pre.contentHash}->${post.contentHash} " +
                            "attempt=$attempt elapsed=${elapsed}ms"
                    )
                    return true
                }

                Log.d(
                    "AuraAgent",
                    "$label verification poll: " +
                        "screen=${if (post == null) "<unreadable>" else "unchanged"} " +
                        "attempt=$attempt elapsed=${elapsed}ms"
                )

                sleep(intervalMs)
            }

            Log.w(
                "AuraAgent",
                "$label content verification TIMEOUT: screen unchanged " +
                    "package=${pre.packageName} nodes=${pre.nodeCount} " +
                    "hash=${pre.contentHash} elapsed=${now() - start}ms"
            )
            return false
        }

        /**
         * Deliver [onComplete] exactly once per invocation, whatever
         * happens to [block] - a normal return, a crash, or a
         * cancellation.
         *
         * The chat UI keeps its loading flag (`isSending`) set until the
         * agent's completion callback fires, so a loop that died without
         * calling it left the spinner spinning forever and lost the
         * task's final message. Every exit from the agent loop goes
         * through this wrapper, which is what makes that impossible:
         *
         *   normal       block returns the final message -> delivered
         *   crash        block throws -> [crashedMessage] delivered
         *   cancelled    block is cancelled (stopAgentLoop, a newer task,
         *                or service teardown) -> [stoppedMessage]
         *                delivered, then CancellationException rethrown
         *                so the Job stays cancelled
         *
         * `onComplete` fires from the `finally`, so cancellation does
         * not skip it, and exactly once, because there is exactly one
         * call site. Sits in the companion (like `verifyOpenApp`) so the
         * guarantee is testable in a JVM unit test.
         */
        suspend fun runWithGuaranteedCompletion(
            block: suspend () -> String,
            onComplete: (String) -> Unit,
            stoppedMessage: String = "The task was stopped before it finished.",
            crashedMessage: String = "The task stopped unexpectedly.",
        ): String {
            var message = crashedMessage
            try {
                message = block()
            } catch (e: CancellationException) {
                message = stoppedMessage
                throw e
            } catch (e: Exception) {
                Log.e("AuraAgent", "Agent loop crashed", e)
                message = crashedMessage
            } finally {
                onComplete(message)
            }
            return message
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

        /**
         * Where one clause ends and the next begins.
         *
         * Read by `shouldAutoComplete` to answer "did the owner ask for
         * more than one thing", and by `isSearchTaskComplete` to answer
         * the sharper version of it - "is there a clause *after* the
         * search". `brain.planner.CONJUNCTIONS` is the server's copy, and
         * `test_the_device_conjunctions_cover_the_planners` asserts this
         * one covers it: every difference has to make the device call a
         * request multi-step where the planner calls it single, because
         * that direction costs a round trip and the other ends a task
         * early.
         */
        val multiStepKeywords = listOf(
            " và ", " rồi ", " sau đó ", " tiếp ",
            " and ", " then ", " after ", " to ",
            ",", ";"
        )

        /**
         * Words that ask for a result to be picked, not merely found.
         *
         * One declaration, two readers: `isSearchTaskComplete` uses it to
         * decline - a request that wants a result played is not finished
         * by a submit - and `isSelectionTaskComplete` uses it to accept.
         * The two used to hold identical copies, which is the shape every
         * drift in this file has taken.
         *
         * Narrower than `brain.planner.SELECTION_CUES`, and deliberately:
         * the planner reads its cues only in a trailing clause, so bare
         * "open" is safe there and would be read here as a launch. The
         * device keeps the qualified forms ("open first", "mở bài") and
         * leaves the ambiguous bare verbs to the server.
         */
        val selectionCues = listOf(
            "play", "select", "pick", "open first", "first song", "first result", "first video",
            "phát", "chơi", "chọn", "nghe", "mở bài", "bài hát đầu tiên", "kết quả đầu tiên"
        )

        /**
         * Is there another job after the search?
         *
         * `isSearchTaskComplete` stops the loop as soon as a query is
         * submitted, which is right when the search was the last thing
         * asked for and wrong when it was not. "open YouTube and search
         * Minecraft" ends at the submit; "open YouTube and search
         * Minecraft then open settings" does not, and used to, because
         * the only question asked was whether the request wanted a
         * *selection* - so a trailing clause of any other kind was
         * invisible.
         *
         * Position matters, which is why this is not simply "does the
         * request contain a conjunction": the working case has one, before
         * the search. Only a separator to the *right* of the last search
         * verb means work remains.
         */
        fun hasClauseAfterSearch(request: String): Boolean {
            val low = request.lowercase().trim()

            val lastVerb = AuraActionExecutor.SEARCH_VERBS
                .map { low.lastIndexOf(it.trim()) }
                .maxOrNull() ?: -1

            if (lastVerb < 0) {
                return false
            }

            return multiStepKeywords.any { keyword ->
                low.indexOf(keyword, lastVerb) > lastVerb
            }
        }

        /**
         * May the loop stop here without asking the server?
         *
         * Not "is the goal met" - the server owns that. `brain/task_graph.py`
         * reads the plan against what happened and `is_finished`/`is_stuck`
         * are its answer, and the model ends a task by saying so with the
         * `complete` action. This function decides something narrower and
         * purely local: whether one navigation step so obviously *was* the
         * whole request that spending another round trip to be told
         * `complete` is waste.
         *
         * That makes it an optimisation, and an optimisation that is wrong
         * costs a truncated task - the loop reports success after launching
         * an app the owner wanted searched. So it has to be conservative in
         * one direction only: when it is unsure, it must keep going and let
         * the server answer.
         *
         * Two ways a request asks for more than one thing, and this used to
         * test only the first:
         *
         *   a conjunction   "open YouTube *and* search Minecraft"
         *   two verbs       "mở YouTube tìm nhạc" - no conjunction at all
         *
         * `brain.planner.plan_for` decomposes both, and its `CONJUNCTIONS`
         * comment claims this list is deliberately the same set. It was not:
         * the planner read "mở YouTube tìm nhạc" as five steps while this
         * returned true on the launch, so the loop stopped having done the
         * first of them and said "App launched successfully!". Six phrasings
         * behaved that way; `test_no_multi_step_request_satisfies_the_device_early_exit`
         * enumerates them against the real planner rather than trusting
         * either comment.
         *
         * The verb test is containment, not a prefix, because the second
         * verb is by definition not at the front. That makes "open research
         * app" read as multi-step, since "research" contains "search" - one
         * extra round trip, and the server says `complete`. The other
         * direction would have been a silently unfinished task, so the
         * false positive is the side to be wrong on.
         *
         * The conjunction list stays a superset of the planner's: it keeps
         * " tiếp " and " to ", and bare "," and ";" where the planner wants
         * a following space. Every one of those differences makes this
         * answer "more than one thing" where the planner says one, which is
         * the safe direction - the device asks, and the server decides.
         */
        fun shouldAutoComplete(request: String, action: AgentAction): Boolean {
            val act = action.action
            if (act != "open_app" && act != "home" && act != "open_notifications" && act != "open_quick_settings") {
                return false
            }

            val reqLower = request.lowercase().trim()

            if (multiStepKeywords.any { keyword -> reqLower.contains(keyword) }) {
                return false
            }

            // The second half of "more than one thing". Shares the one
            // vocabulary with the query sanitiser and with the server's
            // planner, so a verb added in one place is not missing here.
            val asksForSearch = AuraActionExecutor.SEARCH_VERBS.any { verb ->
                reqLower.contains(verb.trim())
            }

            return !asksForSearch
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

        /**
         * What an action was aimed at.
         *
         * A package name for `open_app`, whose target lives in a different
         * field from everything else's, and a node id for the rest.
         *
         * Extracted because three things used to answer this question
         * separately and one of them answered it wrongly. `actionKey` read
         * `action.nodeId` for every kind, which is null for a launch - so
         * the retry key was the literal string "open_app:null" for *every*
         * app. Two failed attempts at Chrome and the next launch, of any
         * app at all, was refused outright with "failed repeatedly. Target
         * is not actionable." One app's failures vetoed another app's first
         * attempt, and nothing in the message said so.
         *
         * The server keys actions by `(kind, target)`, so this is also the
         * definition the two sides now share.
         */
        fun actionTarget(action: AgentAction): String {
            return when (action.action) {
                "open_app" -> action.packageName.orEmpty()
                "home", "back", "submit", "open_notifications", "open_quick_settings" -> ""
                else -> action.nodeId.orEmpty()
            }
        }

        /**
         * One action's identity for the retry guard.
         *
         * Shares `actionTarget` with the history formatter, so an action
         * the server counts and an action this map counts are the same
         * action. When they disagreed, the count that acted and the count
         * that was reported were about different things.
         */
        fun actionKey(action: AgentAction): String {
            return "${action.action}:${actionTarget(action)}"
        }

        /**
         * `kind(args)` - the part of a history line before the verdict.
         *
         * Both formatters build on this so the verified and unverified
         * channels cannot drift into different notions of what identifies
         * an action. `brain/agent_mode.py` parses them with one target
         * reader for the same reason: a success and a failure that
         * disagreed would be two server records for one action, and the
         * count on one of them would never reach its bound.
         */
        fun actionSignature(action: AgentAction): String {
            val target = actionTarget(action)

            if (action.action == "input_text") {
                return "input_text($target, \"${action.text.orEmpty()}\")"
            }

            return "${action.action}($target)"
        }

        fun formatActionHistory(action: AgentAction): String {
            return "${actionSignature(action)} [VERIFIED]"
        }

        /**
         * One action that did not work, for `failed_actions`.
         *
         * `verdict` is the device's own `ExecutionResult` name rather than
         * a taxonomy invented for the wire: FAILED means the gesture could
         * not be performed, UNVERIFIED means it was performed and the
         * postcondition was not observed. Section 11 turns on exactly that
         * distinction, and a single "it did not work" would throw away the
         * more useful half.
         *
         * The count is on the line because this map already keeps it, and
         * because every tick re-sends the whole list - a line per attempt
         * would make the server's arithmetic depend on how many ticks
         * happened to have passed.
         */
        fun formatActionFailure(action: AgentAction, verdict: String, count: Int): String {
            return "${actionSignature(action)} [$verdict x$count]"
        }

        /**
         * Is a search-only request finished by this submit?
         *
         * The same bargain `shouldAutoComplete` makes, at a later point in
         * the task: the server owns "is the goal met", and this decides
         * only whether the loop may stop without spending a round trip to
         * be told `complete`. So it has to decline wherever more work was
         * asked for.
         *
         * Two ways more work is asked for, and this used to see one:
         *
         *   a selection   "search Minecraft and play the first video"
         *   any clause    "search Minecraft then open settings"
         *
         * The second was invisible. `brain.planner.plan_for` reads a
         * trailing clause as another step; this returned true at the
         * submit, so the loop reported the search done and the trailing
         * job never happened. Six phrasings behaved that way, in both
         * languages, including three whose trailing verb ("tap", "click",
         * "bấm") the selection list does not carry - which is why the fix
         * is positional rather than another word added to a list.
         */
        fun isSearchTaskComplete(request: String, action: AgentAction, completedActions: List<String>): Boolean {
            val reqLower = request.lowercase().trim()
            val isSearchReq = AuraActionExecutor.SEARCH_VERBS.any { reqLower.contains(it.trim()) }
            if (!isSearchReq) return false

            if (selectionCues.any { reqLower.contains(it) }) {
                return false
            }

            // Work named after the search is still work. Positional, not a
            // containment test: the ordinary two-clause request ("open
            // YouTube and search Minecraft") has a conjunction too, before
            // the search, and must keep ending here.
            if (hasClauseAfterSearch(reqLower)) {
                return false
            }

            val isSubmitted = action.action == "submit" || (action.action == "input_text" && completedActions.any { it.contains("[VERIFIED]") && it.startsWith("input_text") })
            val hasSubmittedHistory = completedActions.any { it.contains("[VERIFIED]") && (it.startsWith("submit") || it.startsWith("input_text")) }

            return isSubmitted || hasSubmittedHistory
        }

        fun isSelectionTaskComplete(request: String, action: AgentAction, completedActions: List<String>): Boolean {
            val reqLower = request.lowercase().trim()

            if (selectionCues.none { reqLower.contains(it) }) return false

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



        /**
         * The foreground app, as an ordinary chat message should know it.
         *
         * Answers "app gì vậy?" without a screenshot or a vision model:
         * the package comes from the active window, the label from
         * PackageManager, and the activity only when a window-state event
         * has named one for *this* package - `activityFor`'s rule against
         * pairing one app's package with another's screen applies here
         * exactly as it does in the agent snapshot.
         *
         * Null when no service is connected or nothing is known yet -
         * never a guess. Safe to call from the main thread, where the
         * chat ViewModel lives.
         */
        fun currentForegroundApp(): ForegroundApp? {
            val service = instance.get() ?: return null

            val root = service.rootInActiveWindow
            val pkg = root?.packageName?.toString()
            root?.recycle()

            // The remembered window backs the answer up for the instant a
            // transition leaves no active window to ask.
            val window = service.lastWindow.get()

            val resolved = pkg?.trim()?.takeIf { it.isNotEmpty() }
                ?: window?.packageName?.trim()?.takeIf { it.isNotEmpty() }
                ?: return null

            val label = runCatching {
                service.packageManager.getApplicationLabel(
                    service.packageManager.getApplicationInfo(resolved, 0)
                ).toString()
            }.getOrDefault(resolved)

            return ForegroundApp(resolved, activityFor(window, resolved), label)
        }

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

/**
 * The foreground app, as an ordinary chat message carries it.
 *
 * Deliberately metadata only: a package name, a label, and an activity
 * when one is honestly known. Screen *text* keeps travelling through
 * [ScreenObservationService] behind its own switch, so this answer never
 * becomes a second, ungated copy of observation.
 */
data class ForegroundApp(
    val packageName: String,
    val activity: String?,
    val label: String,
)
