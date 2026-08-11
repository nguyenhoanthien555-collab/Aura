package com.aura.companion.ui.hub

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.ChatBubbleOutline
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Memory
import androidx.compose.material.icons.filled.MonitorHeart
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.RemoveRedEye
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.WifiTethering
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.NavigationRow
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.StatusDot
import com.aura.companion.ui.components.StatusTone

/**
 * The Control Hub landing screen.
 *
 * One status card that answers "is Aura up, on which provider, and am I
 * the one it is talking to", then a plain list of sections. The section
 * list is deliberately not the settings themselves - STEP 3 said don't
 * put every setting on the home screen - and chat stays a tap away rather
 * than buried under a hamburger.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HubScreen(
    viewModel: HubViewModel,
    onOpenSection: (String) -> Unit,
    onOpenChat: () -> Unit,
    onBack: () -> Unit,
) {

    val state by viewModel.state.collectAsStateWithLifecycle()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Aura") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    IconButton(onClick = viewModel::refresh) {
                        Icon(
                            Icons.Filled.Refresh,
                            contentDescription = "Refresh",
                            tint = if (state.loading) {
                                MaterialTheme.colorScheme.primary
                            } else {
                                MaterialTheme.colorScheme.onSurfaceVariant
                            },
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState()),
        ) {

            StatusCard(state = state)

            state.notice?.let { notice ->
                NoticeCard(
                    text = notice.text,
                    tone = when (notice.kind) {
                        Notice.Kind.Info -> StatusTone.Neutral
                        Notice.Kind.Warning -> StatusTone.Warning
                        Notice.Kind.Error -> StatusTone.Bad
                    },
                )
            }

            SettingsSection(title = "Control Hub") {

                NavigationRow(
                    title = "Aura",
                    subtitle = "Connection, provider, version",
                    icon = Icons.Filled.Face,
                    onClick = { onOpenSection(HubRoutes.AURA) },
                )

                NavigationRow(
                    title = "AI & Models",
                    subtitle = "Provider, model, API keys",
                    icon = Icons.Filled.Psychology,
                    onClick = { onOpenSection(HubRoutes.MODELS) },
                )

                NavigationRow(
                    title = "Awareness",
                    subtitle = "Screen observation",
                    icon = Icons.Filled.Visibility,
                    onClick = { onOpenSection(HubRoutes.AWARENESS) },
                )

                NavigationRow(
                    title = "Memory",
                    subtitle = "Recall, profile, history",
                    icon = Icons.Filled.Memory,
                    onClick = { onOpenSection(HubRoutes.MEMORY) },
                )

                NavigationRow(
                    title = "Proactive",
                    subtitle = "Unprompted messages",
                    icon = Icons.Filled.Bolt,
                    onClick = { onOpenSection(HubRoutes.PROACTIVE) },
                )

                NavigationRow(
                    title = "Vision",
                    subtitle = "Image understanding",
                    icon = Icons.Filled.RemoveRedEye,
                    onClick = { onOpenSection(HubRoutes.VISION) },
                )

                NavigationRow(
                    title = "Voice",
                    subtitle = "Text to speech, speech to text",
                    icon = Icons.AutoMirrored.Filled.VolumeUp,
                    onClick = { onOpenSection(HubRoutes.VOICE) },
                )

                NavigationRow(
                    title = "Notifications",
                    subtitle = "Companion messages",
                    icon = Icons.Filled.Notifications,
                    onClick = { onOpenSection(HubRoutes.NOTIFICATIONS) },
                )

                NavigationRow(
                    title = "Agent & Tools",
                    subtitle = "What Aura may do, and what needs approval",
                    icon = Icons.Filled.Build,
                    onClick = { onOpenSection(HubRoutes.TOOLS) },
                )

                NavigationRow(
                    title = "Privacy",
                    subtitle = "What leaves this phone, and API keys",
                    icon = Icons.Filled.Shield,
                    onClick = { onOpenSection(HubRoutes.PRIVACY) },
                )

                NavigationRow(
                    title = "Diagnostics",
                    subtitle = "What is reachable, and why not",
                    icon = Icons.Filled.MonitorHeart,
                    onClick = { onOpenSection(HubRoutes.DIAGNOSTICS) },
                )

                NavigationRow(
                    title = "General",
                    subtitle = "Appearance, advanced",
                    icon = Icons.Filled.Tune,
                    onClick = { onOpenSection(HubRoutes.GENERAL) },
                )

                NavigationRow(
                    title = "Connection",
                    subtitle = "Server URL, token",
                    icon = Icons.Filled.WifiTethering,
                    onClick = { onOpenSection(HubRoutes.CONNECTION) },
                )
            }

            SettingsSection(title = "Chat") {

                NavigationRow(
                    title = "Open chat",
                    subtitle = "Conversation, messages",
                    icon = Icons.Filled.ChatBubbleOutline,
                    onClick = onOpenChat,
                )
            }
        }
    }
}

/**
 * "Is Aura reachable and answering from the provider I chose?"
 *
 * The three values that answer that in one glance: connection state,
 * provider state, and - the one the whole fallback feature exists for -
 * whether the active provider is a substitute.
 *
 * WHY THIS READS `reach` AND NOT `loaded`
 * ---------------------------------------
 * It used to say "Disconnected" whenever `GET /api/settings` failed, which
 * on a deployment predating the Control Hub API meant the headline
 * contradicted the chat tab working in the background. Reachability comes
 * from `/api/health` now; a missing settings API is a second line, not a
 * different verdict.
 */
@Composable
private fun StatusCard(state: HubUiState) {

    val server = state.server

    val connected = state.connected

    val provider = server.providers.firstOrNull { it.name == server.health.active }
        ?: server.providers.firstOrNull { it.name == server.health.requested }

    val label = provider?.label ?: server.health.active.ifBlank { "—" }

    // What the server told us about itself, whichever route answered.
    // `/api/health` reports a version even when `/api/settings` is absent,
    // which is exactly the case this card has to render honestly.
    val version = server.config.app.version.ifBlank { server.version }

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 20.dp, vertical = 6.dp),
    ) {

        SurfaceCard {

            Column(modifier = Modifier.padding(20.dp)) {

                Row(verticalAlignment = Alignment.CenterVertically) {

                    Column(modifier = Modifier.weight(1f)) {

                        Text(
                            text = when {
                                connected && server.health.inFallback ->
                                    "Running on a fallback"
                                connected -> "Connected"
                                state.loading -> "Connecting…"
                                // Something answered on that address, but
                                // not as Aura with this token.
                                server.reach == ServerReach.Connected ->
                                    "Server reachable"
                                else -> "Disconnected"
                            },
                            style = MaterialTheme.typography.titleLarge,
                        )

                        Spacer(Modifier.height(4.dp))

                        Text(
                            text = when {
                                // Reachable, authenticated, but this build
                                // of the app knows endpoints that server
                                // does not. Say which half works.
                                connected && server.settingsProblem != null ->
                                    "Chat works. Settings unavailable."
                                connected -> "$label is answering"
                                state.error != null -> state.error
                                else -> "Enter your server address to connect"
                            },
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    StatusDot(
                        tone = when {
                            connected &&
                                !server.health.inFallback &&
                                server.settingsProblem == null -> StatusTone.Good
                            connected -> StatusTone.Warning
                            state.loading -> StatusTone.Neutral
                            else -> StatusTone.Bad
                        },
                        size = 14.dp,
                    )
                }

                if (version.isNotBlank()) {
                    Spacer(Modifier.height(10.dp))
                    Text(
                        text = "Aura $version",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }

        if (!state.device.isConfigured) {
            Spacer(Modifier.height(10.dp))
            NoticeCard(
                text = "No server connected. Set your server address and token " +
                    "in Connection first.",
                tone = StatusTone.Warning,
            )
        } else if (connected && server.settingsProblem != null) {
            // Not an error card: nothing is broken from the user's side and
            // there is nothing for them to fix on the phone. It explains
            // why the sections below are read-only.
            Spacer(Modifier.height(10.dp))
            NoticeCard(
                text = server.settingsProblem +
                    " The sections below are read-only until the server is " +
                    "updated. Chat is unaffected.",
                tone = StatusTone.Warning,
            )
        } else if (!connected && !state.loading) {
            Spacer(Modifier.height(10.dp))
            NoticeCard(
                text = state.error
                    ?: "Could not reach Aura. Check the server address, or try again.",
                tone = StatusTone.Bad,
            )
        }
    }
}

/**
 * The hub's card surface.
 *
 * Reused by every hub screen through [HubSection] - same radius, same
 * tonal step, same feel.
 */
@Composable
fun SurfaceCard(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Box(
        modifier = modifier
            .fillMaxWidth()
            .background(
                color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
                shape = RoundedCornerShape(20.dp),
            ),
    ) {
        content()
    }
}

/**
 * Shared scaffold for every hub section: back arrow, title, optional
 * subtitle, the section's body.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HubSection(
    title: String,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    onRefresh: (() -> Unit)? = null,
    content: @Composable () -> Unit,
) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(title)
                        if (subtitle != null) {
                            Text(
                                text = subtitle,
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    if (onRefresh != null) {
                        IconButton(onClick = onRefresh) {
                            Icon(
                                Icons.Filled.Refresh,
                                contentDescription = "Refresh",
                            )
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        Column(
            modifier = modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 20.dp, vertical = 8.dp),
        ) {
            content()
        }
    }
}

/** Route constants for the hub's navigation graph. */
object HubRoutes {
    const val HUB = "hub"
    const val AURA = "hub/aura"
    const val MODELS = "hub/models"
    const val AWARENESS = "hub/awareness"
    const val MEMORY = "hub/memory"
    const val PROACTIVE = "hub/proactive"
    const val VISION = "hub/vision"
    const val VOICE = "hub/voice"
    const val NOTIFICATIONS = "hub/notifications"
    const val TOOLS = "hub/tools"
    const val PRIVACY = "hub/privacy"
    const val DIAGNOSTICS = "hub/diagnostics"
    const val GENERAL = "hub/general"
    const val CONNECTION = "hub/connection"
}
