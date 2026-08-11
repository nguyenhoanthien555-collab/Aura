package com.aura.companion.ui.hub

import android.content.Context
import android.provider.Settings
import android.view.accessibility.AccessibilityManager
import androidx.core.app.NotificationManagerCompat

/**
 * What this *device* currently permits, as opposed to what the user has
 * asked for in the app.
 *
 * STEP 12's distinction lives here. "Screen observation is on" in Aura's
 * own settings and "the accessibility service is enabled in Android" are
 * two different facts, and a hub that conflates them tells the user their
 * setting is broken when in truth the OS never granted the capability.
 *
 * Every value is read fresh rather than cached: the user leaves for the
 * system settings screen, grants something, and comes back, and a cached
 * answer would still say denied.
 */
data class DevicePermissions(
    val agentServiceEnabled: Boolean = false,
    val observerServiceEnabled: Boolean = false,
    val notificationsPermitted: Boolean = false,
) {
    companion object {

        fun read(context: Context): DevicePermissions {

            val enabled = enabledAccessibilityServices(context)

            return DevicePermissions(
                agentServiceEnabled = enabled.any {
                    it.endsWith(AGENT_SERVICE) || it.contains(AGENT_SERVICE)
                },
                observerServiceEnabled = enabled.any {
                    it.endsWith(OBSERVER_SERVICE) || it.contains(OBSERVER_SERVICE)
                },
                notificationsPermitted = NotificationManagerCompat.from(context)
                    .areNotificationsEnabled(),
            )
        }

        /**
         * The accessibility services Android considers enabled.
         *
         * Read from `Settings.Secure` rather than from
         * `AccessibilityManager.getEnabledAccessibilityServiceList`,
         * because the latter reports only services matching a feedback
         * type and both of Aura's declare `feedbackGeneric` - which the
         * manager filters out of the default query. The secure setting is
         * the list the system UI itself writes.
         *
         * Falls back to the manager if the setting is unreadable.
         */
        private fun enabledAccessibilityServices(context: Context): List<String> {

            val fromSettings = runCatching {
                Settings.Secure.getString(
                    context.contentResolver,
                    Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES,
                )
            }.getOrNull()

            if (!fromSettings.isNullOrBlank()) {
                return fromSettings.split(':').filter { it.isNotBlank() }
            }

            val manager = context.getSystemService(AccessibilityManager::class.java)
                ?: return emptyList()

            return runCatching {
                manager.getEnabledAccessibilityServiceList(
                    android.accessibilityservice.AccessibilityServiceInfo.FEEDBACK_ALL_MASK
                ).mapNotNull { it.resolveInfo?.serviceInfo?.name }
            }.getOrDefault(emptyList())
        }

        private const val AGENT_SERVICE =
            "com.aura.companion.accessibility.AuraAccessibilityService"

        private const val OBSERVER_SERVICE =
            "com.aura.companion.screen.ScreenObservationService"
    }
}
