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

    fun startAgentLoop(request: String, onComplete: (String) -> Unit) {
        agentJob?.cancel()
        agentJob = scope.launch {
            var stepCount = 0
            val maxSteps = 10
            var currentRequest = request
            var finalMessage = "Task failed to complete."

            while (stepCount < maxSteps) {
                stepCount++
                Log.d("AuraAgent", "Starting agent step $stepCount for task: $currentRequest")

                val root = rootInActiveWindow
                if (root == null) {
                    Log.d("AuraAgent", "No active window root found. Retrying in 1s...")
                    delay(1000)
                    continue
                }

                val nodeMap = mutableMapOf<String, AccessibilityNodeInfo>()
                val tree = AccessibilityNodeSerializer.serialize(root, nodeMap)

                val displayMetrics = resources.displayMetrics
                val deviceState = DeviceState(
                    width = displayMetrics.widthPixels,
                    height = displayMetrics.heightPixels
                )

                val activePackage = root.packageName?.toString().orEmpty()
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
                    userRequest = currentRequest
                )

                val jsonContext = Json.encodeToJsonElement(snapshot).jsonObject

                Log.d("AuraAgent", "Sending screen snapshot to backend...")
                val result = repository.send("agent_tick", jsonContext)

                var actionExecuted = false
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
                                nodeMap.values.forEach { it.recycle() }
                                break
                            }

                            val targetNode = nodeMap[action.nodeId]
                            if (safetyGuard.checkAction(action, targetNode)) {
                                Log.d("AuraAgent", "Executing action: ${action.action}")
                                val success = executor.execute(action, nodeMap)
                                Log.d("AuraAgent", "Action success: $success")
                                actionExecuted = success
                            } else {
                                finalMessage = "Action blocked for safety: attempted ${action.action} containing sensitive targets."
                                Log.w("AuraAgent", finalMessage)
                                nodeMap.values.forEach { it.recycle() }
                                break
                            }
                        } else {
                            finalMessage = "Failed to parse action from server: $reply"
                            Log.e("AuraAgent", finalMessage)
                            nodeMap.values.forEach { it.recycle() }
                            break
                        }
                    }
                    is AuraResult.Failed -> {
                        finalMessage = "Aura server request failed: ${result.error.userMessage}"
                        Log.e("AuraAgent", finalMessage)
                        nodeMap.values.forEach { it.recycle() }
                        break
                    }
                }

                // Recycle nodes
                nodeMap.values.forEach { it.recycle() }

                if (actionExecuted) {
                    // Wait for screen state to settle
                    delay(1500)
                } else {
                    finalMessage = "Action failed to execute or UI did not respond."
                    break
                }
            }

            if (stepCount >= maxSteps) {
                finalMessage = "Task timed out: maximum number of steps reached."
            }

            onComplete(finalMessage)
        }
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
