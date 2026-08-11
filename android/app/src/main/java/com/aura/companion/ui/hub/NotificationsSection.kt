package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.NotificationsActive
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SelectRow
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.StatusRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.ToggleRow
import com.aura.companion.work.NotificationScheduler

/**
 * Settings → Notifications.
 *
 * THREE FACTS, NOT ONE SWITCH
 * ---------------------------
 * STEP 13 forbids claiming delivery works when it does not, and there are
 * three independent ways for it to not work:
 *
 *   The app setting     whether this phone polls Aura at all
 *   Android's grant     whether a notification may be posted once collected
 *   The server's switch `server.companion.enabled`, which decides whether
 *                       Aura addresses companion messages to devices
 *
 * Any one of them being off means nothing arrives, and the reason differs
 * every time. So each is its own row and the delivery status row names
 * whichever one is actually blocking.
 *
 * POLLING, NOT PUSH
 * -----------------
 * `NotificationWorker` is a fifteen-minute `PeriodicWorkRequest`. There is
 * no FCM in this app and no background scheduler on the server, so a
 * message waits in the queue until the next poll. The closing card says
 * so; STEP 25 forbids implying otherwise.
 */
@Composable
fun NotificationsSection(
    state: HubUiState,
    viewModel: HubViewModel,
    onRequestPermission: () -> Unit,
    onOpenSystemSettings: () -> Unit,
    onBack: () -> Unit,
) {
    val context = LocalContext.current

    // Read fresh, not remembered: the user leaves to grant the permission
    // and returns, and a cached answer would still say denied.
    val permissions = DevicePermissions.read(context)

    val companion = state.server.config.server.companion

    HubSection(
        title = "Notifications",
        subtitle = "How Aura reaches you when the app is closed",
        onBack = onBack,
        onRefresh = viewModel::refresh,
    ) {

        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        SettingsSection(
            title = "This device",
            subtitle = "Stored on the phone. Other devices are unaffected.",
        ) {

            ToggleRow(
                title = "Companion notifications",
                subtitle = "Check Aura for messages and show them here",
                icon = Icons.Filled.NotificationsActive,
                checked = state.device.notificationsEnabled,
                onCheckedChange = { enabled ->
                    viewModel.setNotifications(enabled)
                    // Sync the poller now rather than at the next launch.
                    // Writing the flag alone would leave the scheduled work
                    // in place, and "off" has to mean off immediately.
                    NotificationScheduler.sync(context, enabled)
                },
            )

            RowDivider()

            SelectRow(
                title = "Android permission",
                value = if (permissions.notificationsPermitted) {
                    "Granted"
                } else {
                    "Not granted"
                },
                subtitle = if (permissions.notificationsPermitted) {
                    "Aura may post notifications on this phone"
                } else {
                    "Tap to allow notifications for Aura"
                },
                icon = Icons.Filled.PhoneAndroid,
                onClick = if (permissions.notificationsPermitted) {
                    onOpenSystemSettings
                } else {
                    onRequestPermission
                },
            )
        }

        // ------------------------------------------------------------------
        // On the server
        // ------------------------------------------------------------------

        SettingsSection(
            title = "On the server",
            subtitle = "Applies to every device connected to this Aura",
        ) {

            ToggleRow(
                title = "Send companion messages",
                subtitle = "When off, Aura keeps its remarks to itself entirely",
                icon = Icons.Filled.Cloud,
                checked = companion.enabled,
                pending = "server.companion.enabled" in state.pending,
                lockedReason = state.lockedReason("server.companion.enabled"),
                onCheckedChange = {
                    viewModel.setFlag("server.companion.enabled", it)
                },
            )
        }

        // ------------------------------------------------------------------
        // The honest answer
        // ------------------------------------------------------------------

        SettingsSection(title = "Delivery") {

            StatusRow(
                title = "Messages reach this phone",
                value = deliveryLabel(
                    appEnabled = state.device.notificationsEnabled,
                    permitted = permissions.notificationsPermitted,
                    configured = state.device.isConfigured,
                    serverEnabled = !state.settingsAvailable || companion.enabled,
                ),
                subtitle = "Checked every 15 minutes while the app is closed",
                icon = Icons.Filled.Schedule,
                tone = if (
                    state.device.notificationsEnabled &&
                    permissions.notificationsPermitted &&
                    state.device.isConfigured &&
                    (!state.settingsAvailable || companion.enabled)
                ) {
                    StatusTone.Good
                } else {
                    StatusTone.Warning
                },
            )
        }

        if (state.device.notificationsEnabled && !permissions.notificationsPermitted) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = "Notifications are on in Aura but Android has not granted " +
                    "the permission, so nothing can be shown. Aura still " +
                    "collects its messages - you will see them in chat.",
                tone = StatusTone.Warning,
            )
        }

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "Aura is checked on a schedule rather than pushed to. There " +
                "is no Firebase in this app, so a message can wait up to " +
                "fifteen minutes - and Android may delay that further to save " +
                "battery.",
            tone = StatusTone.Neutral,
            icon = Icons.Filled.Schedule,
        )

        Spacer(Modifier.height(32.dp))
    }
}

/**
 * Which of the four conditions is actually blocking delivery.
 *
 * Ordered by what the user can do about it first. Saying "Off" when the
 * real problem is a denied OS permission is the failure STEP 13 is about.
 */
private fun deliveryLabel(
    appEnabled: Boolean,
    permitted: Boolean,
    configured: Boolean,
    serverEnabled: Boolean,
): String = when {
    !configured -> "Not connected"
    !appEnabled -> "Off"
    !permitted -> "Blocked by Android"
    !serverEnabled -> "Off on the server"
    else -> "Yes"
}
