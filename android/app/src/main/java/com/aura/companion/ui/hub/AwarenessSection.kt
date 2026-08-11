package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.Timer
import androidx.compose.material.icons.filled.TouchApp
import androidx.compose.material.icons.filled.Visibility
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
import kotlin.math.roundToInt

/**
 * Settings → Awareness.
 *
 * FOUR STATES, NOT TWO
 * --------------------
 * STEP 12's requirement is the whole reason this screen is split the way it
 * is. "Screen observation" can be:
 *
 *   Enabled              the user wants it and Android permits it
 *   Permission required  the user wants it, Android has not granted it
 *   Unavailable          the server was built without the capability
 *   Disabled             the user turned it off
 *
 * A toggle alone cannot say which. So the device switch and the OS grant
 * are separate rows, and the grant row is the one that opens the system
 * settings screen. Nothing here pretends a switch can grant a permission.
 *
 * The server half (`server.screen.enabled`) and the device half
 * (`screenObservationEnabled`) are also separate, because they are: the
 * server can accept screen context while this phone declines to send any,
 * and one phone declining says nothing about another.
 */
@Composable
fun AwarenessSection(
    state: HubUiState,
    viewModel: HubViewModel,
    onOpenAccessibilitySettings: () -> Unit,
    onBack: () -> Unit,
) {
    val context = LocalContext.current

    // Read on every recomposition rather than remembered: the user leaves
    // for the system settings screen and comes back, and a cached value
    // would still say the permission was denied.
    val permissions = DevicePermissions.read(context)

    val server = state.server

    val serverScreen = server.config.server.screen

    HubSection(
        title = "Awareness",
        subtitle = "What Aura can see of this phone",
        onBack = onBack,
        onRefresh = viewModel::refresh,
    ) {

        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        // ------------------------------------------------------------------
        // This device
        // ------------------------------------------------------------------

        SettingsSection(
            title = "This device",
            subtitle = "Stored on the phone. Other devices are unaffected.",
        ) {

            ToggleRow(
                title = "Screen observation",
                subtitle = "Send what is on screen so Aura has context",
                icon = Icons.Filled.Visibility,
                checked = state.device.screenObservationEnabled,
                onCheckedChange = viewModel::setScreenObservation,
            )

            RowDivider()

            ToggleRow(
                title = "Send screenshots",
                subtitle = "Images, not just the text Aura reads from the screen",
                icon = Icons.Filled.PhotoCamera,
                checked = state.device.uploadScreenshots,
                enabled = state.device.screenObservationEnabled,
                lockedReason = if (state.device.screenObservationEnabled) {
                    null
                } else {
                    "Turn on screen observation first"
                },
                onCheckedChange = viewModel::setUploadScreenshots,
            )
        }

        // ------------------------------------------------------------------
        // Android permissions
        //
        // Facts about the OS, and the one action that can change them.
        // ------------------------------------------------------------------

        SettingsSection(
            title = "Android permissions",
            subtitle = "Granted by the system, not by Aura",
        ) {

            SelectRow(
                title = "Screen reading",
                value = permissions.observerServiceEnabled.grantLabel(),
                subtitle = if (permissions.observerServiceEnabled) {
                    "Aura's observer service is enabled"
                } else {
                    "Tap to enable it in Android's accessibility settings"
                },
                icon = Icons.Filled.PhoneAndroid,
                onClick = onOpenAccessibilitySettings,
            )

            RowDivider()

            SelectRow(
                title = "Agent actions",
                value = permissions.agentServiceEnabled.grantLabel(),
                subtitle = if (permissions.agentServiceEnabled) {
                    "Aura can tap and type on your behalf"
                } else {
                    "Tap to enable it in Android's accessibility settings"
                },
                icon = Icons.Filled.TouchApp,
                onClick = onOpenAccessibilitySettings,
            )
        }

        if (state.device.screenObservationEnabled &&
            !permissions.observerServiceEnabled
        ) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = "Screen observation is on in Aura but Android has not " +
                    "granted the accessibility permission, so nothing is being " +
                    "sent. Enable Aura's observer service above.",
                tone = StatusTone.Warning,
            )
        }

        // ------------------------------------------------------------------
        // Server side
        // ------------------------------------------------------------------

        SettingsSection(
            title = "On the server",
            subtitle = "Applies to every device connected to this Aura",
        ) {

            ToggleRow(
                title = "Accept screen context",
                subtitle = "When off, Aura ignores screen updates from any device",
                icon = Icons.Filled.Cloud,
                checked = serverScreen.enabled,
                pending = "server.screen.enabled" in state.pending,
                lockedReason = state.lockedReason("server.screen.enabled"),
                onCheckedChange = { viewModel.setFlag("server.screen.enabled", it) },
            )

            RowDivider()

            // Read-only on purpose. `server.screen.min_interval` is not in
            // the server's allow-list, so a slider here would be a control
            // that 422s - or worse, one that moves and changes nothing. The
            // number is still worth showing: it explains why updates arrive
            // at the rate they do.
            StatusRow(
                title = "Minimum interval",
                value = "${serverScreen.minInterval.roundToInt()}s",
                icon = Icons.Filled.Timer,
            )
        }

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "Aura reads the screen only while the app that owns it is in " +
                "the foreground and observation is on. It is never recorded, and " +
                "turning the switch off stops it immediately.",
            tone = StatusTone.Neutral,
        )

        Spacer(Modifier.height(32.dp))
    }
}

/** "Granted" / "Not granted" - the OS's answer, not the app's setting. */
private fun Boolean.grantLabel(): String = if (this) "Granted" else "Not granted"
