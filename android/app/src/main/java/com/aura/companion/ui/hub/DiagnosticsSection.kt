package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Wifi
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.StatusRow
import com.aura.companion.ui.components.StatusTone
import kotlin.math.roundToLong

/**
 * Settings → Diagnostics.
 *
 * THE SCREEN THAT EXPLAINS A PARTIAL FAILURE
 * ------------------------------------------
 * Aura can be up, authenticated, answering chat, and still unable to
 * serve this app's settings API - that is exactly what a deployment older
 * than the phone looks like. A single "Connected / Disconnected" light
 * cannot express it, and the app used to resolve the contradiction by
 * calling a working server dead.
 *
 * So the first card here is [ServerReach] rendered one rung at a time.
 * Each row is an independently observed fact with its own request behind
 * it, and the first one that fails is the answer. Nothing on this screen
 * is derived from another row.
 *
 * WHY IT IS ALL READ-ONLY
 * -----------------------
 * A diagnostics screen that changes things cannot be trusted to report
 * things. The only action is refresh, which re-runs the same three
 * requests the hub uses.
 *
 * NO SECRETS, BY CONSTRUCTION
 * ---------------------------
 * Every value here comes from `/api/health` or `/api/providers/health`,
 * neither of which returns a key, a header or an exception message - see
 * `_per_provider_health` in `server/routes/settings.py`. The token is not
 * rendered anywhere on this screen, not even masked.
 */
@Composable
fun DiagnosticsSection(
    state: HubUiState,
    viewModel: HubViewModel,
    onBack: () -> Unit,
) {
    val server = state.server

    val reach = server.reach

    HubSection(
        title = "Diagnostics",
        subtitle = "What is reachable, and why not",
        onBack = onBack,
        onRefresh = viewModel::refresh,
    ) {

        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        // ------------------------------------------------------------------
        // The ladder. Four requests, four independent answers.
        // ------------------------------------------------------------------

        SettingsSection(
            title = "Connection",
            subtitle = "Each step is checked separately",
        ) {

            StatusRow(
                title = "Server address",
                value = if (state.device.isConfigured) "Set" else "Not set",
                subtitle = state.device.serverUrl.ifBlank { "Nothing to connect to" },
                icon = Icons.Filled.Cloud,
                tone = if (state.device.isConfigured) {
                    StatusTone.Good
                } else {
                    StatusTone.Warning
                },
            )

            RowDivider()

            StatusRow(
                title = "Something answered",
                value = reach.atLeast(ServerReach.Connected).outcome(state.loading),
                subtitle = if (reach.atLeast(ServerReach.Connected)) {
                    "A server responded at that address"
                } else {
                    "No response - wrong address, or nothing listening"
                },
                icon = Icons.Filled.Wifi,
                tone = reach.atLeast(ServerReach.Connected).tone(state.loading),
            )

            RowDivider()

            // The rung that matters most and is easiest to misread. This is
            // `GET /api/health`, which is itself behind `verify_token`, so a
            // pass here proves the server is Aura *and* took the token.
            StatusRow(
                title = "Token accepted",
                value = reach.atLeast(ServerReach.Authenticated).outcome(state.loading),
                subtitle = when {
                    reach.atLeast(ServerReach.Authenticated) ->
                        "Aura answered /api/health. Chat works from here."
                    reach == ServerReach.Connected ->
                        state.error ?: "The server did not accept this token"
                    else -> "Not reached yet"
                },
                icon = Icons.Filled.Lock,
                tone = reach.atLeast(ServerReach.Authenticated).tone(state.loading),
            )

            RowDivider()

            StatusRow(
                title = "Settings API",
                value = when {
                    state.settingsAvailable -> "Available"
                    !reach.atLeast(ServerReach.Authenticated) -> "Not reached"
                    else -> state.settingsAccess.label
                },
                subtitle = when {
                    state.settingsAvailable ->
                        "This server can be configured from the phone"
                    !reach.atLeast(ServerReach.Authenticated) ->
                        "Checked after the token"
                    else -> state.settingsAccess.reason
                },
                icon = Icons.Filled.Settings,
                tone = when {
                    state.settingsAvailable -> StatusTone.Good
                    !reach.atLeast(ServerReach.Authenticated) -> StatusTone.Neutral
                    // A missing endpoint is a limitation, a refused token is a
                    // fault, and a rate limit is neither. The colour follows
                    // the actual state rather than assuming the mildest one.
                    else -> state.settingsAccess.tone
                },
            )

            RowDivider()

            StatusRow(
                title = "Provider chain",
                value = when {
                    server.health.chain.isEmpty() -> "Not reported"
                    server.health.inFallback -> "On a fallback"
                    server.health.ready -> "Serving"
                    else -> "Not ready"
                },
                subtitle = server.health.chain
                    .takeIf { it.isNotEmpty() }
                    ?.joinToString(" → ")
                    // An empty chain has two causes and they are not the same
                    // news: a server that reported none, or a request that
                    // never came back. The provider routes used to fail in
                    // silence, which left this row saying the first while the
                    // second had happened.
                    ?: server.providersError?.userMessage
                    ?: "This server did not report its provider chain",
                icon = Icons.Filled.Bolt,
                tone = when {
                    server.health.chain.isEmpty() && server.providersError != null ->
                        StatusTone.Warning
                    server.health.chain.isEmpty() -> StatusTone.Neutral
                    server.health.inFallback -> StatusTone.Warning
                    server.health.ready -> StatusTone.Good
                    else -> StatusTone.Bad
                },
            )
        }

        settingsNotice(state.settingsAccess)
            ?.takeIf { state.connected }
            ?.let { notice ->
                Spacer(Modifier.height(12.dp))
                NoticeCard(
                    text = notice,
                    tone = state.settingsAccess.tone,
                    icon = Icons.Filled.Info,
                )
            }

        // ------------------------------------------------------------------
        // The server, as it describes itself.
        // ------------------------------------------------------------------

        SettingsSection(title = "Server") {

            StatusRow(
                title = "Version",
                value = server.config.app.version
                    .ifBlank { server.version }
                    .ifBlank { "—" },
                icon = Icons.Filled.Info,
            )

            RowDivider()

            StatusRow(
                title = "Uptime",
                value = server.uptimeSeconds.uptimeLabel(),
                subtitle = "Since the Aura process last started",
                icon = Icons.Filled.Schedule,
            )

            RowDivider()

            StatusRow(
                title = "Answering",
                value = server.health.active.ifBlank { "—" },
                subtitle = server.health.requested
                    .takeIf { it.isNotBlank() && it != server.health.active }
                    ?.let { "You asked for $it" },
                icon = Icons.Filled.Bolt,
                tone = when {
                    server.health.active.isBlank() -> StatusTone.Neutral
                    server.health.inFallback -> StatusTone.Warning
                    else -> StatusTone.Good
                },
            )
        }

        // ------------------------------------------------------------------
        // Subsystems, rendered from the server's own report.
        // ------------------------------------------------------------------

        if (server.runtime.isNotEmpty()) {

            SettingsSection(
                title = "Subsystems",
                subtitle = "What this deployment actually built",
            ) {

                // The server's key order, not a list this app decides, so a
                // build reporting something new still shows it.
                server.runtime.entries.forEachIndexed { index, (key, value) ->

                    if (index > 0) RowDivider()

                    StatusRow(
                        title = key.subsystemLabel(),
                        value = value,
                        tone = value.subsystemTone(),
                    )
                }
            }
        }

        // ------------------------------------------------------------------
        // Per-provider state, where the server reports it.
        // ------------------------------------------------------------------

        if (server.health.providers.isNotEmpty()) {

            SettingsSection(
                title = "Providers",
                subtitle = "Reported without calling them - no tokens spent",
            ) {

                server.health.providers.entries.forEachIndexed { index, (name, it) ->

                    if (index > 0) RowDivider()

                    StatusRow(
                        title = name,
                        value = it.state.ifBlank {
                            if (it.configured) "configured" else "no key"
                        },
                        subtitle = when {
                            it.problem.isNotBlank() ->
                                "Could not be read (${it.problem})"
                            it.state == "active" -> "Serving your requests now"
                            it.state == "standby" -> "Next in the chain if this one fails"
                            it.state == "failed" -> "Tried and did not answer"
                            it.state == "idle" -> "Has a key but is not in the chain"
                            it.state == "unconfigured" -> "No API key stored"
                            else -> null
                        },
                        tone = when (it.state) {
                            "active" -> StatusTone.Good
                            "standby" -> StatusTone.Neutral
                            "failed", "error" -> StatusTone.Bad
                            else -> StatusTone.Neutral
                        },
                    )
                }
            }
        }

        server.health.problems.forEach { problem ->
            Spacer(Modifier.height(12.dp))
            NoticeCard(text = problem, tone = StatusTone.Bad)
        }

        if (!state.connected && state.error != null) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(text = state.error, tone = StatusTone.Bad)
        }

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "Nothing on this screen changes anything, and none of it " +
                "contains your token or an API key. Refresh re-runs the same " +
                "checks the hub does when you open it.",
            tone = StatusTone.Neutral,
        )

        Spacer(Modifier.height(32.dp))
    }
}

/** "Yes" / "No", or "Checking" while a request is still out. */
private fun Boolean.outcome(loading: Boolean): String = when {
    this -> "Yes"
    loading -> "Checking"
    else -> "No"
}

private fun Boolean.tone(loading: Boolean): StatusTone = when {
    this -> StatusTone.Good
    loading -> StatusTone.Neutral
    else -> StatusTone.Bad
}

/**
 * `uptime_seconds` as something readable.
 *
 * Coarse on purpose: the question this answers is "did it restart
 * recently", so a seconds-precise figure would be noise at every scale
 * except the one that matters - a server up for less than a minute.
 */
private fun Double.uptimeLabel(): String {

    val total = roundToLong()

    if (total <= 0L) return "—"

    val days = total / 86_400
    val hours = (total % 86_400) / 3_600
    val minutes = (total % 3_600) / 60

    return when {
        days > 0 -> "${days}d ${hours}h"
        hours > 0 -> "${hours}h ${minutes}m"
        minutes > 0 -> "${minutes}m"
        else -> "${total}s"
    }
}

/**
 * The `/api/health` runtime keys, made readable.
 *
 * Unknown keys fall through to a de-underscored form rather than being
 * dropped: the key set is a pinned server contract, and a build that adds
 * one should still be legible here without shipping a new app.
 */
private fun String.subsystemLabel(): String = when (this) {
    "llm_provider" -> "Language model"
    "memory" -> "Memory"
    "vision" -> "Vision"
    "voice_output" -> "Speech output"
    "voice_input" -> "Speech input"
    "screen" -> "Screen context"
    "companion" -> "Companion messages"
    "proactive" -> "Proactive"
    else -> replace('_', ' ').replaceFirstChar { it.uppercase() }
}

/**
 * Colour a subsystem value without pretending "disabled" is a fault.
 *
 * Most of these are off by default and correctly so - vision, voice and
 * proactive. Only an explicit unavailability reads as bad.
 *
 * Prefix-matched rather than compared whole: the server appends a reason
 * to some values ("unavailable (ValueError)" when the provider chain
 * could not be built), and an exact match would have coloured the one
 * genuinely broken subsystem the same neutral grey as a switched-off one.
 */
private fun String.subsystemTone(): StatusTone {

    val value = lowercase()

    return when {
        value.startsWith("enabled") ||
            value.startsWith("connected") ||
            value.startsWith("healthy") -> StatusTone.Good

        value.startsWith("unavailable") ||
            value.startsWith("error") ||
            value.startsWith("unknown") -> StatusTone.Bad

        else -> StatusTone.Neutral
    }
}
