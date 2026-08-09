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

    private sealed interface ExecutionResult {
        object Success : ExecutionResult
        object Failed : ExecutionResult
        object Blocked : ExecutionResult
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
                val result = repository.send("agent_tick", jsonContext)

                // Recycle tree node references immediately after sending request to prevent leaks
                nodeMap.values.forEach { it.recycle() }

                when (result) {
                    is AuraResult.Ok -> {
                        val reply = result.value.reply.trim()
                        Log.d("AuraAgent", "Backend response: $reply")

                        val action = runCatching {
                            Json.decodeFromString<AgentAction>(reply)
                        }.getOrNull()

                        if (action != null) {
                            if (action.action == "complete") {
                                finalMessage = action.message ?: "Task completed successfully!"
                                Log.d("AuraAgent", "Task complete: $finalMessage")
                                break
                            }

                            // Check consecutive failures for this node/action
                            val actionKey = "${action.action}:${action.nodeId}"
                            if ((failedActionsCount[actionKey] ?: 0) >= 2) {
                                lastActionError = "Action ${action.action} on ${action.nodeId} failed repeatedly. Target is not actionable."
                                Log.w("AuraAgent", lastActionError!!)
                                continue
                            }

                            val execResult = executeActionWithRecovery(action, tree)
                            if (execResult is ExecutionResult.Success) {
                                failedActionsCount.remove(actionKey)
                                delay(1500) // Wait for screen state to settle after success
                            } else if (execResult is ExecutionResult.Blocked) {
                                finalMessage = "Action blocked for safety: attempted ${action.action} containing sensitive targets."
                                break
                            } else {
                                failedActionsCount[actionKey] = (failedActionsCount[actionKey] ?: 0) + 1
                                lastActionError = "Action ${action.action} on ${action.nodeId} failed. Target not clickable or not found."
                            }
                        } else {
                            finalMessage = "Failed to parse action from server: $reply"
                            Log.e("AuraAgent", finalMessage)
                            break
                        }
                    }
                    is AuraResult.Failed -> {
                        finalMessage = "Aura server request failed: ${result.error.userMessage}"
                        Log.e("AuraAgent", finalMessage)
                        break
                    }
                }
            }

            if (stepCount >= maxSteps) {
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

            val root = rootInActiveWindow
            if (root == null) {
                Log.d("AuraAgent", "No active window root found during execution. Retrying in 1s...")
                delay(1000)
                continue
            }

            val freshNodeMap = mutableMapOf<String, AccessibilityNodeInfo>()

            // Resolve target node using AuraActionExecutor
            val (resolvedNode, resolvedNodeMeta) = if (action.nodeId != null) {
                executor.resolveNode(action.nodeId, oldTree, root, freshNodeMap)
            } else {
                Pair(null, null)
            }

            // Run SafetyGuard check on the resolved node
            if (!safetyGuard.checkAction(action, resolvedNode)) {
                freshNodeMap.values.forEach { it.recycle() }
                root.recycle()
                Log.w("AuraAgent", "Action blocked for safety: attempted ${action.action} containing sensitive targets.")
                return ExecutionResult.Blocked
            }

            // Execute the action with the resolved node
            val success = executor.executeWithNode(action, resolvedNode, resolvedNodeMeta, freshNodeMap)

            // Recycle all fresh node references to prevent leaks
            freshNodeMap.values.forEach { it.recycle() }
            root.recycle()

            if (success) {
                // Verify action
                delay(1000)
                val newRoot = rootInActiveWindow
                if (newRoot != null) {
                    Log.d("AuraAgent", "Action verified. Fresh screen package: ${newRoot.packageName}")
                    newRoot.recycle()
                }
                return ExecutionResult.Success
            } else {
                Log.w("AuraAgent", "Attempt $attempt failed for action ${action.action}")
                if (attempt < maxAttempts) {
                    delay(1000)
                }
            }
        }
        return ExecutionResult.Failed
    }

    fun stopAgentLoop() {
        agentJob?.cancel()
        agentJob = null
    }

    companion object {
        private val instance = AtomicReference<AuraAccessibilityService?>(null)

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
