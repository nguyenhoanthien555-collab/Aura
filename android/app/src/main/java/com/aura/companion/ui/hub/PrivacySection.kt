package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.PhoneAndroid
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.Textsms
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.NavigationRow
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.StatusRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.ToggleRow

/**
 * Settings → Privacy.
 *
 * WHAT THIS SCREEN IS FOR
 * -----------------------
 * One place that answers "what does Aura know about me, and how did it
 * get there". The two switches at the top are the only two things in this
 * app that cause data to leave the phone, so they belong on the privacy
 * screen even though Awareness also has them - they write the same
 * [com.aura.companion.data.settings.SettingsStore], so there is one value
 * and two ways to reach it, not two settings that can disagree.
 *
 * Everything below the switches is a fact, and the facts are checked
 * rather than reassuring. The transport row reads the actual scheme of
 * the saved URL; the key-storage row reads what the server said about its
 * own ability to persist a key.
 *
 * WHAT IS DELIBERATELY MISSING
 * ----------------------------
 * There is no "delete everything Aura remembers" button, because there is
 * no endpoint behind it. `DELETE /api/sessions/{id}` exists but, as
 * `server/session.py` says itself, a session is metadata only - the
 * conversation lives in `memory/`, so a button wired to it would report
 * success having erased nothing. That is the fake control this phase
 * forbids, so the screen says plainly where the data is instead, and the
 * actions that do exist link to the screen that owns them.
 */
@Composable
fun PrivacySection(
    state: HubUiState,
    viewModel: HubViewModel,
    onOpenSection: (String) -> Unit,
    onBack: () -> Unit,
) {
    val device = state.device

    val server = state.server

    HubSection(
        title = "Privacy",
        subtitle = "What leaves this phone, and what Aura keeps",
        onBack = onBack,
        onRefresh = viewModel::refresh,
    ) {

        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        SettingsSection(
            title = "What this phone sends",
            subtitle = "Stored on the phone. Nothing here is sent to Aura.",
        ) {

            ToggleRow(
                title = "Screen text",
                subtitle = "What is on screen, as text, so Aura has context",
                icon = Icons.Filled.Visibility,
                checked = device.screenObservationEnabled,
                onCheckedChange = viewModel::setScreenObservation,
            )

            RowDivider()

            ToggleRow(
                title = "Screenshots",
                subtitle = "The image itself, not just the text",
                icon = Icons.Filled.PhotoCamera,
                checked = device.uploadScreenshots,
                enabled = device.screenObservationEnabled,
                lockedReason = if (device.screenObservationEnabled) {
                    null
                } else {
                    "Screen text is off, so nothing is sent at all"
                },
                onCheckedChange = viewModel::setUploadScreenshots,
            )
        }

        if (device.screenObservationEnabled) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = if (device.uploadScreenshots) {
                    "Aura receives both the text on your screen and the images " +
                        "themselves, while the app you are using is in the " +
                        "foreground. Turning either switch off stops it at once."
                } else {
                    "Aura receives the text on your screen while the app you are " +
                        "using is in the foreground. No images are sent."
                },
                tone = StatusTone.Neutral,
            )
        }

        SettingsSection(
            title = "How it travels",
            subtitle = "Between this phone and your Aura",
        ) {

            StatusRow(
                title = "Encryption",
                value = if (device.isSecure) "HTTPS" else "HTTP",
                subtitle = if (device.isSecure) {
                    "Encrypted in transit"
                } else {
                    "Not encrypted - anything on the path can read it"
                },
                icon = Icons.Filled.Lock,
                tone = when {
                    !device.isConfigured -> StatusTone.Neutral
                    device.isSecure -> StatusTone.Good
                    else -> StatusTone.Warning
                },
            )

            RowDivider()

            StatusRow(
                title = "Destination",
                value = if (device.isConfigured) "Your server" else "None",
                subtitle = device.serverUrl.ifBlank {
                    "No server address, so nothing is sent anywhere"
                },
                icon = Icons.Filled.Cloud,
            )
        }

        SettingsSection(
            title = "Kept on this phone",
            subtitle = "In Android's encrypted preferences, backed by the keystore",
        ) {

            StatusRow(
                title = "Server address and token",
                value = "Encrypted",
                subtitle = "Removed when you disconnect",
                icon = Icons.Filled.PhoneAndroid,
                tone = StatusTone.Good,
            )

            RowDivider()

            // Worth stating because people assume the opposite: the chat
            // list is state in memory, not a database. Nothing survives the
            // app being killed except the eight preference keys.
            StatusRow(
                title = "Your conversation",
                value = "Not stored here",
                subtitle = "Messages live on the Aura server, not in this app",
                icon = Icons.Filled.Textsms,
            )
        }

        SettingsSection(
            title = "Kept on the server",
            subtitle = "Where Aura runs, not on this phone",
        ) {

            StatusRow(
                title = "API keys",
                value = when {
                    !state.settingsAvailable -> "Unknown"
                    server.keysPersistent -> "Stored encrypted"
                    else -> "Until restart only"
                },
                subtitle = when {
                    !state.settingsAvailable ->
                        "This server did not report how it stores keys"
                    server.keysPersistent ->
                        "Encrypted on the server. Never sent back to this phone."
                    else -> server.keyStorageNote.ifBlank {
                        "The server has no secret to encrypt them with, so keys " +
                            "are held in memory only."
                    }
                },
                icon = Icons.Filled.Key,
                tone = when {
                    !state.settingsAvailable -> StatusTone.Neutral
                    server.keysPersistent -> StatusTone.Good
                    else -> StatusTone.Warning
                },
            )

            RowDivider()

            StatusRow(
                title = "What Aura remembers",
                value = if (server.config.memory.pipeline) "On" else "Off",
                subtitle = "Conversations and what it has learned about you, " +
                    "held where Aura is deployed",
                icon = Icons.Filled.Memory,
                tone = if (server.config.memory.pipeline) {
                    StatusTone.Good
                } else {
                    StatusTone.Neutral
                },
            )
        }

        SettingsSection(
            title = "Change or remove",
            subtitle = "Each of these lives on the screen that owns it",
        ) {

            NavigationRow(
                title = "API keys",
                subtitle = "Replace or delete a stored key",
                icon = Icons.Filled.Key,
                onClick = { onOpenSection(HubRoutes.MODELS) },
            )

            RowDivider()

            NavigationRow(
                title = "Memory",
                subtitle = "Turn recall and the profile off",
                icon = Icons.Filled.Memory,
                onClick = { onOpenSection(HubRoutes.MEMORY) },
            )

            RowDivider()

            NavigationRow(
                title = "Disconnect",
                subtitle = "Erase the address and token from this phone",
                icon = Icons.Filled.PhoneAndroid,
                onClick = { onOpenSection(HubRoutes.CONNECTION) },
            )
        }

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "Turning memory off stops Aura learning anything new, but it " +
                "does not erase what it already has - no part of Aura exposes a " +
                "way to do that over the network, so this app cannot offer one. " +
                "It is deleted where Aura is deployed.",
            tone = StatusTone.Neutral,
        )

        if (device.isConfigured && !device.isSecure) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = "This connection is plain HTTP. Your token, your messages " +
                    "and anything read from your screen travel unencrypted. Use " +
                    "HTTPS for anything beyond your own network.",
                tone = StatusTone.Warning,
            )
        }

        Spacer(Modifier.height(32.dp))
    }
}
