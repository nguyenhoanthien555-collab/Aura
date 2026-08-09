package com.aura.companion.screen

import android.accessibilityservice.AccessibilityService
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import com.aura.companion.AuraApplication
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.settings.SettingsStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

/**
 * Reads what is on screen and forwards a summary to Aura.
 *
 * Three rules govern this class, in order of importance:
 *
 *  1. It sends nothing unless the user has switched screen observation on
 *     in Settings. Enabling the service in the system UI is necessary but
 *     not sufficient - the app-level toggle is checked on every event, so
 *     turning it off stops the flow immediately rather than at the next
 *     restart.
 *
 *  2. It sends *text*, not pixels. The accessibility tree is what the
 *     companion actually reasons over, and a screenshot costs orders of
 *     magnitude more bandwidth for information the text already carries.
 *
 *  3. It sends rarely. Android fires window-content events continuously -
 *     every cursor blink in a text field is one - so the throttle and the
 *     duplicate check below are not an optimisation, they are the
 *     difference between a companion and a battery fire.
 */
class ScreenObservationService : AccessibilityService() {

    private lateinit var settings: SettingsStore
    private lateinit var repository: AuraRepository

    private val job = SupervisorJob()
    private val scope = CoroutineScope(job)

    private val throttle = ObservationThrottle()

    @Volatile
    private var inFlight: Job? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        val container = (application as AuraApplication).container
        settings = container.settings
        repository = container.repository
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {

        if (event == null) return

        // Rule 1. Checked first, before anything is read from the screen.
        if (!::settings.isInitialized) return
        if (!settings.current.screenObservationEnabled) return
        if (!settings.current.isConfigured) return

        when (event.eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> Unit
            else -> return
        }

        val packageName = event.packageName?.toString().orEmpty()

        if (packageName.isBlank()) return

        // Never report our own UI back to the server. It would be a loop
        // whose content is the conversation the loop is generating.
        if (packageName == this.packageName) return

        if (!throttle.allowsAttempt()) return

        val root = rootInActiveWindow ?: return

        val text = runCatching { collectText(root) }.getOrNull().orEmpty()

        if (text.length < MIN_TEXT_LENGTH) return

        if (!throttle.accept(packageName, text)) return

        // One request at a time. A screen that changes while a slow request
        // is open should not queue a second - the newer screen will be
        // picked up by the next event anyway.
        if (inFlight?.isActive == true) return

        val application = runCatching {
            packageManager.getApplicationLabel(
                packageManager.getApplicationInfo(packageName, 0)
            ).toString()
        }.getOrDefault(packageName)

        inFlight = scope.launch {
            repository.sendScreen(
                application = application,
                packageName = packageName,
                screenText = text,
            )
            // The result is deliberately dropped. The server decides
            // whether to say anything, and it says it through the
            // notification channel - not through a return value here.
        }
    }

    override fun onInterrupt() {
        inFlight?.cancel()
    }

    override fun onDestroy() {
        super.onDestroy()
        scope.cancel()
    }

    /**
     * Flatten the visible text of a window.
     *
     * Breadth-limited and depth-limited: some apps expose thousands of
     * nodes, and walking all of them on the main thread during a scroll is
     * how an accessibility service becomes a jank complaint.
     *
     * Password fields are skipped outright.
     */
    private fun collectText(root: AccessibilityNodeInfo): String {

        val parts = mutableListOf<String>()

        fun walk(node: AccessibilityNodeInfo?, depth: Int) {

            if (node == null) return
            if (depth > MAX_DEPTH) return
            if (parts.size > MAX_PARTS) return

            // Never read a password field, regardless of settings.
            if (node.isPassword) return

            node.text?.toString()?.trim()?.let {
                if (it.isNotEmpty() && it.length <= MAX_PART_LENGTH) parts.add(it)
            }

            node.contentDescription?.toString()?.trim()?.let {
                if (it.isNotEmpty() && it.length <= MAX_PART_LENGTH) parts.add(it)
            }

            for (index in 0 until node.childCount) {
                walk(node.getChild(index), depth + 1)
            }
        }

        walk(root, 0)

        return parts.joinToString(" ").take(MAX_TEXT_LENGTH)
    }

    private companion object {
        const val MAX_DEPTH = 12
        const val MAX_PARTS = 300
        const val MAX_PART_LENGTH = 500
        const val MAX_TEXT_LENGTH = 8_000
        const val MIN_TEXT_LENGTH = 24
    }
}
