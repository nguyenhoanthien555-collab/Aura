package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.WifiTethering
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.StatusRow
import com.aura.companion.ui.components.StatusTone

/**
 * Settings → Aura.
 *
 * Read-only, and that is the point: everything here is a fact the server
 * reported. It is the screen to open when the question is "what is Aura
 * actually running", before changing anything.
 */
@Composable
fun AuraSection(
    state: HubUiState,
    onRefresh: () -> Unit,
    onBack: () -> Unit,
) {
    HubSection(
        title = "Aura",
        subtitle = "Status and connection",
        onBack = onBack,
        onRefresh = onRefresh,
    ) {

        val server = state.server

        AnimatedNotice(
            text = state.notice?.text,
            tone = state.notice.tone(),
        )

        SettingsSection(title = "Status") {

            StatusRow(
                title = "Connection",
                value = when {
                    state.connected -> "Connected"
                    state.loading -> "Connecting"
                    // The address answered, but not as Aura accepting this
                    // token. A different verdict from nothing answering.
                    server.reach == ServerReach.Connected -> "Not authenticated"
                    state.device.isConfigured -> "Unreachable"
                    else -> "Not configured"
                },
                tone = when {
                    state.connected -> StatusTone.Good
                    state.loading -> StatusTone.Neutral
                    else -> StatusTone.Bad
                },
                icon = Icons.Filled.WifiTethering,
            )

            RowDivider()

            // Present only when it is the answer to a question the user
            // would otherwise ask: the server is up, so why is everything
            // below read-only? The row names the actual failure - a rate
            // limit and a missing endpoint are not the same news.
            if (state.connected && server.settingsError != null) {

                val access = state.settingsAccess

                StatusRow(
                    title = "Settings API",
                    value = access.label,
                    subtitle = access.reason,
                    tone = access.tone,
                    icon = Icons.Filled.Info,
                )

                RowDivider()
            }

            StatusRow(
                title = "Transport",
                value = if (state.device.isSecure) "HTTPS" else "HTTP",
                tone = if (state.device.isSecure) StatusTone.Good else StatusTone.Warning,
                icon = Icons.Filled.Lock,
            )

            RowDivider()

            StatusRow(
                title = "Version",
                // `/api/health` carries a version even where `/api/settings`
                // does not, so the row is filled on an older server too.
                value = server.config.app.version
                    .ifBlank { server.version }
                    .ifBlank { "—" },
                icon = Icons.Filled.Info,
            )
        }

        SettingsSection(
            title = "Provider",
            subtitle = "Which model is answering right now",
        ) {

            StatusRow(
                title = "Requested",
                value = server.health.requested.ifBlank { server.config.llm.provider }
                    .ifBlank { "—" },
                icon = Icons.Filled.Psychology,
            )

            RowDivider()

            StatusRow(
                title = "Answering",
                value = server.health.active.ifBlank { "—" },
                tone = when {
                    // Keyed on the health document, not on the settings
                    // one: `/api/providers/health` is a separate request
                    // and can answer when settings does not.
                    server.health.active.isBlank() -> StatusTone.Neutral
                    server.health.inFallback -> StatusTone.Warning
                    server.health.ready -> StatusTone.Good
                    else -> StatusTone.Bad
                },
                icon = Icons.Filled.Bolt,
            )

            RowDivider()

            StatusRow(
                title = "Model",
                // The primary provider's model, not `llm.model`. That field
                // is Gemini's, so reading it directly showed a Gemini model
                // name on a phone whose primary was Claude - a fact about
                // Aura that was simply untrue. See `ModelSettingTest`.
                value = state.activeModel.ifBlank { "—" },
                icon = Icons.Filled.Cloud,
            )

            if (server.health.chain.size > 1) {

                RowDivider()

                StatusRow(
                    title = "Fallback chain",
                    value = server.health.chain.joinToString(" → "),
                )
            }
        }

        if (server.health.inFallback) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = "Aura is answering from ${server.health.active} because " +
                    "${server.health.chain.firstOrNull() ?: "the primary provider"} " +
                    "is not available. Check its API key under AI & Models.",
                tone = StatusTone.Warning,
            )
        }

        server.health.problems.forEach { problem ->
            Spacer(Modifier.height(12.dp))
            NoticeCard(text = problem, tone = StatusTone.Bad)
        }

        if (server.restartRequired) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = "Some saved settings need an Aura restart before they " +
                    "take effect. Restart it where it is deployed - the app " +
                    "cannot restart the server.",
                tone = StatusTone.Warning,
            )
        }

        Spacer(Modifier.height(32.dp))
    }
}

/** Map a [Notice] to the tone its card should use. */
fun Notice?.tone(): StatusTone = when (this?.kind) {
    Notice.Kind.Warning -> StatusTone.Warning
    Notice.Kind.Error -> StatusTone.Bad
    else -> StatusTone.Neutral
}
