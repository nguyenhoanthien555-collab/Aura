package com.aura.companion.ui.hub

import android.os.Build
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Colorize
import androidx.compose.material.icons.filled.DarkMode
import androidx.compose.material.icons.filled.Fingerprint
import androidx.compose.material.icons.filled.Handyman
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.RestartAlt
import androidx.compose.material.icons.filled.Save
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aura.companion.data.settings.ThemeMode
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.ChoiceDialog
import com.aura.companion.ui.components.DangerActionCard
import com.aura.companion.ui.components.ExpandableSection
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SectionHeader
import com.aura.companion.ui.components.SelectRow
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.StatusRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.TextEntryDialog
import com.aura.companion.ui.components.ToggleRow

/**
 * Settings → General.
 *
 * APPEARANCE IS DEVICE-LOCAL, ON PURPOSE
 * --------------------------------------
 * Theme and dynamic colour never reach `PATCH /api/settings`. A theme is a
 * property of the phone looking at Aura, not of Aura, and two phones
 * pointed at one deployment are allowed to disagree about dark mode. They
 * live in [com.aura.companion.data.settings.SettingsStore] for that reason.
 *
 * Dynamic colour is honest about the platform: below Android 12 there is no
 * wallpaper palette to read, so the row locks rather than offering a switch
 * that changes nothing.
 *
 * ADVANCED IS COLLAPSED
 * ---------------------
 * STEP 14 puts diagnostics behind a disclosure rather than in front of
 * someone who just wanted dark mode. Nothing in it is a secret - the token
 * is deliberately absent, since a screen that prints a bearer token is a
 * screen that leaks one to whoever is looking over your shoulder.
 */
@Composable
fun GeneralSection(
    state: HubUiState,
    viewModel: HubViewModel,
    onBack: () -> Unit,
) {
    var pickingTheme by remember { mutableStateOf(false) }

    var advancedOpen by remember { mutableStateOf(false) }

    var editingTimezone by remember { mutableStateOf(false) }

    val dynamicSupported = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S

    HubSection(
        title = "General",
        subtitle = "Appearance, features, diagnostics",
        onBack = onBack,
        onRefresh = viewModel::refresh,
    ) {

        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        SettingsSection(
            title = "Appearance",
            subtitle = "This phone only",
        ) {

            SelectRow(
                title = "Theme",
                value = state.device.themeMode.label,
                subtitle = "Light, dark, or whatever the phone is doing",
                icon = Icons.Filled.DarkMode,
                onClick = { pickingTheme = true },
            )

            RowDivider()

            ToggleRow(
                title = "Wallpaper colours",
                subtitle = if (dynamicSupported) {
                    "Match Aura's palette to your wallpaper"
                } else {
                    "Needs Android 12 or newer"
                },
                icon = Icons.Filled.Colorize,
                checked = state.device.dynamicColour && dynamicSupported,
                enabled = dynamicSupported,
                lockedReason = if (dynamicSupported) {
                    null
                } else {
                    "This phone's Android version has no wallpaper palette"
                },
                onCheckedChange = viewModel::setDynamicColour,
            )
        }

        // ------------------------------------------------------------------
        // Capabilities that apply to the whole deployment
        // ------------------------------------------------------------------

        SettingsSection(
            title = "Abilities",
            subtitle = "Applies to every device connected to this Aura",
        ) {

            ToggleRow(
                title = "Tools",
                subtitle = "Let Aura use its tools rather than only talking",
                icon = Icons.Filled.Handyman,
                checked = state.server.config.tools.enabled,
                pending = "tools.enabled" in state.pending,
                lockedReason = state.lockedReason("tools.enabled"),
                onCheckedChange = { viewModel.setFlag("tools.enabled", it) },
            )

            RowDivider()

            // Phase 23: settable over PATCH since it was added, with no
            // control anywhere. The deployment that needs this - a container
            // running in UTC whose owner is not - is exactly the one that
            // cannot edit config.yaml. Blank means "the host's own zone".
            SelectRow(
                title = "Aura's time zone",
                value = state.server.config.temporal.timezone.ifBlank {
                    "Host's own zone"
                },
                subtitle = "What \"today\" and quiet hours mean to Aura",
                icon = Icons.Filled.Schedule,
                lockedReason = state.lockedReason("temporal.timezone"),
                onClick = { editingTimezone = true },
            )
        }

        if (state.server.restartRequired) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = "Something you changed is saved but not live yet. Restart " +
                    "Aura where it is deployed to apply it.",
                tone = StatusTone.Warning,
                icon = Icons.Filled.RestartAlt,
            )
        }

        // ------------------------------------------------------------------
        // Advanced
        // ------------------------------------------------------------------

        ExpandableSection(
            title = "Advanced",
            subtitle = "Diagnostics. Nothing here needs changing.",
            expanded = advancedOpen,
            onExpandedChange = { advancedOpen = it },
        ) {

            StatusRow(
                title = "This device",
                value = state.device.deviceId.ifBlank { "—" },
                subtitle = "Generated on this phone, not a hardware ID",
                icon = Icons.Filled.Fingerprint,
            )

            RowDivider()

            StatusRow(
                title = "Server",
                value = state.device.serverUrl.ifBlank { "Not set" },
                subtitle = if (state.device.isSecure) {
                    "Encrypted in transit"
                } else {
                    "Plain HTTP - fine on your own network"
                },
                icon = Icons.Filled.Link,
                tone = when {
                    !state.device.isConfigured -> StatusTone.Neutral
                    state.device.isSecure -> StatusTone.Good
                    else -> StatusTone.Warning
                },
            )

            RowDivider()

            StatusRow(
                title = "Aura version",
                value = state.server.config.app.version.ifBlank { "—" },
                icon = Icons.Filled.Build,
            )

            RowDivider()

            // Whether the server can keep what this app changes. Without a
            // secret to encrypt with, the settings overlay is in-memory and
            // dies with the process - which the user has to know before
            // trusting a toggle to survive a Render redeploy.
            StatusRow(
                title = "Settings storage",
                value = if (state.server.keysPersistent) "Saved" else "Until restart",
                subtitle = state.server.keyStorageNote.ifBlank {
                    "Where changes made from this app are kept"
                },
                icon = Icons.Filled.Save,
                tone = if (state.server.keysPersistent) {
                    StatusTone.Good
                } else {
                    StatusTone.Warning
                },
            )
        }

        SectionHeader(
            title = "Reset",
            subtitle = "Undo every change made from this app",
        )

        DangerActionCard(
            title = "Revert all Aura settings",
            description = "Drop every setting this app has changed and go back to " +
                "the configuration the Aura deployment itself ships with. Your " +
                "connection, theme and memories are untouched.",
            actionLabel = "Revert all",
            confirmBody = "Every server setting changed from this app - provider, " +
                "model, memory, proactive, vision, voice and awareness - goes " +
                "back to the deployment's own configuration. Some changes need " +
                "an Aura restart to take effect.",
            enabled = state.settingsAvailable,
            busy = state.loading,
            onConfirm = { viewModel.resetSettings() },
        )

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "Theme and wallpaper colours are stored on this phone and are " +
                "not part of that reset.",
            tone = StatusTone.Neutral,
        )

        Spacer(Modifier.height(32.dp))
    }

    if (pickingTheme) {
        ChoiceDialog(
            title = "Theme",
            options = ThemeMode.entries.map { it.label },
            selected = state.device.themeMode.label,
            onPick = { picked ->
                ThemeMode.entries.firstOrNull { it.label == picked }?.let {
                    viewModel.setThemeMode(it)
                }
            },
            onDismiss = { pickingTheme = false },
        )
    }

    if (editingTimezone) {
        TextEntryDialog(
            title = "Aura's time zone",
            initial = state.server.config.temporal.timezone,
            label = "IANA zone name",
            help = "For example Europe/Berlin. Left empty, Aura uses the time " +
                "zone of the machine it runs on - which, in a container, is " +
                "usually UTC.",
            onCommit = { viewModel.setText("temporal.timezone", it.trim()) },
            onDismiss = { editingTimezone = false },
        )
    }
}
