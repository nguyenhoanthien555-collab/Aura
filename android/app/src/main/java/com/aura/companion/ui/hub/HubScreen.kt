package com.aura.companion.ui.hub

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.tween
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.expandVertically
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.CutCornerShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import com.aura.companion.ui.theme.AuraIcons
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.NavigationRow
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.contentColour
import com.aura.companion.ui.theme.AuraMotion
import com.aura.companion.ui.theme.auraBackgroundBrush
import com.aura.companion.ui.theme.auraGlass
import com.aura.companion.ui.theme.auraGlassEdge
import com.aura.companion.ui.theme.auraGlassBlur
import com.aura.companion.ui.theme.auraHeroBrush
import com.aura.companion.ui.theme.auraTileBrush
import com.aura.companion.ui.theme.rememberReducedMotion

/**
 * The Control Hub landing screen.
 *
 * WHAT THE LAYOUT IS FOR
 * ----------------------
 * Three bands, in the order the questions get asked: is Aura up and who is
 * answering ([HeroCard]); what is it currently allowed to do ([StatusTile]s);
 * and then, only then, thirteen sections to change any of it. The thirteen
 * used to be one flat list, which made "Vision" and "Connection" look like
 * decisions of equal weight and left the whole screen reading as a
 * preferences pane. They are four named groups now.
 *
 * Chat sits between the glance and the settings, because it is what the app
 * is *for* - not the last row under Diagnostics.
 *
 * WHAT THE ANIMATION IS FOR
 * -------------------------
 * Colour crossfades on state changes, the banner expands rather than
 * appearing, and exactly one thing repeats: the ring around the status dot,
 * and only while a request is genuinely in flight. A companion app that
 * breathes forever is a companion app that keeps the frame pipeline awake
 * all evening. Everything here is also gated on
 * [rememberReducedMotion] - if the platform's animation scale is 0, states
 * change instantly rather than slowly.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HubScreen(
    viewModel: HubViewModel,
    onOpenSection: (String) -> Unit,
    onOpenChat: () -> Unit,
    onBack: () -> Unit,
    bottomBar: @Composable () -> Unit = {},
) {

    val state by viewModel.state.collectAsStateWithLifecycle()

    val reduced = rememberReducedMotion()

    val headline = hubHeadline(state)

    val banner = hubBanner(state)

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Aura") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(AuraIcons.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    IconButton(onClick = viewModel::refresh) {
                        Icon(
                            AuraIcons.Refresh,
                            contentDescription = "Refresh",
                            tint = animateColorAsState(
                                targetValue = if (state.loading) {
                                    MaterialTheme.colorScheme.primary
                                } else {
                                    MaterialTheme.colorScheme.onSurfaceVariant
                                },
                                animationSpec = tween(
                                    AuraMotion.scaled(AuraMotion.Quick, reduced)
                                ),
                                label = "refreshTint",
                            ).value,
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
        bottomBar = bottomBar,
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->

        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(auraBackgroundBrush()),
        ) {

            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentPadding = PaddingValues(
                    start = 20.dp, end = 20.dp, top = 4.dp, bottom = 40.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {

                item(key = "hero") {
                    HeroCard(
                        headline = headline,
                        version = state.server.config.app.version
                            .ifBlank { state.server.version },
                        state = state,
                        reduced = reduced,
                    )
                }

                item(key = "banner") {
                    val showBanner = banner != null && (state.notice == null || banner.text != state.notice?.text)
                    AnimatedVisibility(
                        visible = showBanner,
                        enter = expandVertically(
                            tween(AuraMotion.scaled(AuraMotion.Standard, reduced))
                        ) + fadeIn(tween(AuraMotion.scaled(AuraMotion.Standard, reduced))),
                        exit = shrinkVertically(
                            tween(AuraMotion.scaled(AuraMotion.Quick, reduced))
                        ) + fadeOut(tween(AuraMotion.scaled(AuraMotion.Quick, reduced))),
                    ) {
                        // Held across the exit animation so the text does not
                        // vanish a frame before the card it sits in.
                        banner?.let { NoticeCard(text = it.text, tone = it.tone) }
                    }
                }

                state.notice?.let { notice ->
                    item(key = "notice") {
                        NoticeCard(
                            text = notice.text,
                            tone = when (notice.kind) {
                                Notice.Kind.Info -> StatusTone.Neutral
                                Notice.Kind.Warning -> StatusTone.Warning
                                Notice.Kind.Error -> StatusTone.Bad
                            },
                        )
                    }
                }

                item(key = "ribbon") {
                    StatusRibbon(
                        tiles = hubTiles(state),
                        reduced = reduced,
                        onOpen = onOpenSection,
                    )
                }

                items(HUB_GROUPS, key = { it.title }) { group ->
                    SettingsSection(title = group.title, subtitle = group.subtitle) {
                        group.entries.forEachIndexed { index, entry ->
                            NavigationRow(
                                title = entry.title,
                                subtitle = entry.subtitle,
                                icon = entry.icon,
                                onClick = { onOpenSection(entry.route) },
                            )
                            if (index < group.entries.lastIndex) RowDivider()
                        }
                    }
                }
            }
        }
    }
}

// ----------------------------------------------------------------------
// The hero
// ----------------------------------------------------------------------

/**
 * "Is Aura up, and who is answering me?"
 *
 * Futuristic Dual-Device Synced Mesh Hero Card:
 * Displays Host PC and Handset status connected by a cyber pulse bridge.
 */
@Composable
private fun HeroCard(
    headline: HubHeadline,
    version: String,
    state: HubUiState,
    reduced: Boolean,
) {
    val shape = CutCornerShape(topStart = 18.dp, bottomEnd = 18.dp, topEnd = 4.dp, bottomStart = 4.dp)
    val borderGradient = Brush.horizontalGradient(
        listOf(
            Color(0xFF8B5CF6).copy(alpha = 0.55f),
            Color(0xFF06B6D4).copy(alpha = 0.55f),
        )
    )

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .border(1.dp, borderGradient, shape)
            .auraGlassBlur(
                shape = shape,
                tint = Color(0xFF131224).copy(alpha = 0.85f),
            )
            .background(brush = auraHeroBrush(), shape = shape, alpha = 0.4f)
            .padding(16.dp),
    ) {
        Column(
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // Technical HUD Header Strip
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .size(6.dp)
                            .background(Color(0xFF00E5FF), CircleShape)
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = "SYS // TRI-NODE LINK MATRIX",
                        style = MaterialTheme.typography.labelSmall.copy(
                            fontFamily = FontFamily.Monospace,
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Bold,
                            letterSpacing = 1.sp,
                        ),
                        color = Color(0xFF38BDF8),
                    )
                }

                Surface(
                    shape = CutCornerShape(4.dp),
                    color = Color(0xFF10A37F).copy(alpha = 0.2f),
                    border = androidx.compose.foundation.BorderStroke(0.5.dp, Color(0xFF10A37F)),
                ) {
                    Text(
                        text = "GPT-5.6 LUNA 🌙",
                        style = MaterialTheme.typography.labelSmall.copy(
                            fontFamily = FontFamily.Monospace,
                            fontSize = 9.sp,
                            fontWeight = FontWeight.Bold,
                        ),
                        color = Color(0xFF34D399),
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                    )
                }
            }

            // Dual Device Synced Mesh Graphic
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                // Host Node (Laptop or Render Cloud)
                val isWindows = state.hostTelemetry?.os?.contains("Windows", ignoreCase = true) == true
                val hostIcon = if (isWindows) AuraIcons.Laptop else AuraIcons.Cloud
                val hostTitle = when {
                    state.hostTelemetry != null && state.hostTelemetry.model.isNotBlank() && state.hostTelemetry.model != "Standard PC" -> state.hostTelemetry.model
                    isWindows -> "Host PC"
                    else -> "Render Cloud"
                }
                val hostSub = when {
                    state.hostTelemetry != null -> "CPU ${state.hostTelemetry.cpuPercent.toInt()}% • RAM ${state.hostTelemetry.ramUsedPercent.toInt()}%"
                    isWindows -> "Windows 11"
                    else -> "Cloud Node"
                }

                val nodeShape = CutCornerShape(topStart = 8.dp, bottomEnd = 8.dp, topEnd = 2.dp, bottomStart = 2.dp)

                Surface(
                    shape = nodeShape,
                    color = Color(0xFF0369A1).copy(alpha = 0.22f),
                    border = androidx.compose.foundation.BorderStroke(0.75.dp, Color(0xFF38BDF8).copy(alpha = 0.45f)),
                    modifier = Modifier.weight(1f),
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(
                            imageVector = hostIcon,
                            contentDescription = null,
                            tint = Color(0xFF38BDF8),
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.width(6.dp))
                        Column {
                            Text(
                                text = hostTitle,
                                style = MaterialTheme.typography.labelSmall.copy(
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace,
                                ),
                                color = Color.White,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                text = hostSub,
                                style = MaterialTheme.typography.labelSmall.copy(fontSize = 9.sp),
                                color = Color(0xFF7DD3FC),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }

                // Laser Sync Line with Animated Pulse & Ping
                Row(
                    modifier = Modifier.padding(horizontal = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.Center,
                ) {
                    Box(
                        modifier = Modifier
                            .width(10.dp)
                            .height(1.5.dp)
                            .background(
                                Brush.horizontalGradient(
                                    listOf(Color(0xFF38BDF8), Color(0xFF8B5CF6))
                                )
                            )
                    )
                    Text(
                        text = if (state.pingMs > 0) "${state.pingMs}ms" else "SYNC",
                        style = MaterialTheme.typography.labelSmall.copy(
                            fontSize = 8.sp,
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                        ),
                        color = Color(0xFFC084FC),
                        modifier = Modifier.padding(horizontal = 2.dp),
                        maxLines = 1,
                        softWrap = false,
                    )
                    Box(
                        modifier = Modifier
                            .width(10.dp)
                            .height(1.5.dp)
                            .background(
                                Brush.horizontalGradient(
                                    listOf(Color(0xFF8B5CF6), Color(0xFF34D399))
                                )
                            )
                    )
                }

                // Phone Node
                Surface(
                    shape = nodeShape,
                    color = Color(0xFF065F46).copy(alpha = 0.22f),
                    border = androidx.compose.foundation.BorderStroke(0.75.dp, Color(0xFF34D399).copy(alpha = 0.45f)),
                    modifier = Modifier.weight(1f),
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(
                            imageVector = AuraIcons.DeviceMobile,
                            contentDescription = null,
                            tint = Color(0xFF34D399),
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.width(6.dp))
                        Column {
                            Text(
                                text = state.phoneTelemetry?.let { "${it.batteryPercent}% ${if (it.isCharging) "⚡" else ""}" } ?: "Handset",
                                style = MaterialTheme.typography.labelSmall.copy(
                                    fontWeight = FontWeight.Bold,
                                    fontFamily = FontFamily.Monospace,
                                ),
                                color = Color.White,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                            Text(
                                text = state.phoneTelemetry?.let { "${it.networkType} • A${it.androidVersion.replace("Android ", "")}" } ?: "Android 13",
                                style = MaterialTheme.typography.labelSmall.copy(fontSize = 9.sp),
                                color = Color(0xFF6EE7B7),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                }
            }

            // Headline & Status
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(modifier = Modifier.weight(1f)) {
                    Text(
                        text = headline.title,
                        style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                        color = Color.White,
                    )
                    Spacer(Modifier.height(3.dp))
                    Text(
                        text = headline.detail,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (version.isNotBlank()) {
                        Spacer(Modifier.height(6.dp))
                        Text(
                            text = "Aura Core v$version",
                            style = MaterialTheme.typography.labelSmall.copy(
                                fontSize = 10.sp,
                                fontFamily = FontFamily.Monospace,
                            ),
                            color = Color(0xFF94A3B8),
                        )
                    }
                }

                StatusRing(tone = headline.tone, busy = headline.busy, reduced = reduced)
            }
        }
    }
}

/**
 * The status indicator: a dot, and a halo that only moves when it means
 * something.
 *
 * The halo scales and fades on an infinite transition, which is the one
 * animation in this app that costs a frame per frame. It is created only
 * while [busy] is true, so a settled hub composes no repeating animation at
 * all and the screen goes quiet - which is the difference between a subtle
 * status indicator and a battery drain.
 */
@Composable
private fun StatusRing(tone: StatusTone, busy: Boolean, reduced: Boolean) {

    val colour = animateColorAsState(
        targetValue = tone.contentColour(),
        animationSpec = tween(AuraMotion.scaled(AuraMotion.Standard, reduced)),
        label = "statusTone",
    ).value

    Box(contentAlignment = Alignment.Center, modifier = Modifier.size(44.dp)) {

        if (AuraMotion.mayLoop(reduced = reduced, busy = busy)) {

            val pulse = rememberInfiniteTransition(label = "statusPulse")

            val scale = pulse.animateFloat(
                initialValue = 0.55f,
                targetValue = 1f,
                animationSpec = infiniteRepeatable(
                    animation = tween(AuraMotion.Slow * 2),
                    repeatMode = RepeatMode.Reverse,
                ),
                label = "statusPulseScale",
            ).value

            Box(
                modifier = Modifier
                    .size(44.dp)
                    .scale(scale)
                    .alpha(0.28f)
                    .background(colour, CircleShape),
            )
        } else {
            // A still halo, so the dot does not appear to shrink when a
            // refresh finishes.
            Box(
                modifier = Modifier
                    .size(30.dp)
                    .alpha(0.16f)
                    .background(colour, CircleShape),
            )
        }

        Box(
            modifier = Modifier
                .size(14.dp)
                .background(colour, CircleShape),
        )
    }
}

// ----------------------------------------------------------------------
// The glance: Sleek horizontal status ribbon
// ----------------------------------------------------------------------

/** Sleek horizontal status ribbon replacing the bulky 2x2 tile grid. */
@Composable
private fun StatusRibbon(
    tiles: List<HubTile>,
    reduced: Boolean,
    onOpen: (String) -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        tiles.forEach { tile ->
            CompactStatusChip(
                tile = tile,
                reduced = reduced,
                onClick = { onOpen(tile.route) },
            )
        }
    }
}

/** One compact cyber status chip. */
@Composable
private fun CompactStatusChip(
    tile: HubTile,
    reduced: Boolean,
    onClick: () -> Unit,
) {
    val shape = CutCornerShape(topStart = 8.dp, bottomEnd = 8.dp, topEnd = 2.dp, bottomStart = 2.dp)
    val colour = animateColorAsState(
        targetValue = tile.tone.contentColour(),
        animationSpec = tween(AuraMotion.scaled(AuraMotion.Standard, reduced)),
        label = "chipTone",
    ).value

    Box(
        modifier = Modifier
            .clip(shape)
            .border(0.75.dp, colour.copy(alpha = 0.45f), shape)
            .auraGlassBlur(
                shape = shape,
                tint = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.2f),
            )
            .background(brush = auraTileBrush(), shape = shape, alpha = 0.5f)
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 8.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Icon(
                imageVector = tile.kind.icon(),
                contentDescription = null,
                tint = colour,
                modifier = Modifier.size(15.dp),
            )
            Text(
                text = tile.label,
                style = MaterialTheme.typography.labelSmall.copy(fontSize = 11.sp),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = "•",
                style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                color = MaterialTheme.colorScheme.outlineVariant,
            )
            Text(
                text = tile.value,
                style = MaterialTheme.typography.labelSmall.copy(
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                ),
                color = colour,
                maxLines = 1,
            )
        }
    }
}

private fun HubTileKind.icon(): ImageVector = when (this) {
    HubTileKind.Provider -> AuraIcons.Brain
    HubTileKind.Memory -> AuraIcons.Memory
    HubTileKind.Awareness -> AuraIcons.Vision
    HubTileKind.Proactive -> AuraIcons.Bolt
}

// ----------------------------------------------------------------------
// The sections
// ----------------------------------------------------------------------

/** One navigable hub section. */
private data class HubEntry(
    val title: String,
    val subtitle: String,
    val icon: ImageVector,
    val route: String,
)

private data class HubGroup(
    val title: String,
    val subtitle: String?,
    val entries: List<HubEntry>,
)

/**
 * The thirteen sections, grouped by what they are about.
 *
 * Grouping rather than reordering: every route that existed still exists and
 * still means the same thing, so nothing anybody had learned moved. What
 * changed is that a flat thirteen no longer implies that the switch letting
 * Aura read the screen and the field holding a server URL are the same kind
 * of decision.
 *
 * Declared as data rather than as thirteen calls so the list has one shape,
 * one divider rule, and stable keys for [LazyColumn].
 */
private val HUB_GROUPS = listOf(
    HubGroup(
        title = "// 01. INTELLIGENCE MATRIX",
        subtitle = "How Aura thinks and what it remembers",
        entries = listOf(
            HubEntry(
                "AI & Models", "Provider, model, API keys",
                AuraIcons.Brain, HubRoutes.MODELS,
            ),
            HubEntry(
                "Memory", "Recall, profile, history",
                AuraIcons.Memory, HubRoutes.MEMORY,
            ),
            HubEntry(
                "Vision", "Image understanding",
                AuraIcons.Vision, HubRoutes.VISION,
            ),
            HubEntry(
                "Voice", "Text to speech, speech to text",
                AuraIcons.VolumeUp, HubRoutes.VOICE,
            ),
        ),
    ),
    HubGroup(
        title = "// 02. PRESENCE & DAEMON",
        subtitle = "What Aura may see, and when it may speak first",
        entries = listOf(
            HubEntry(
                "Awareness", "Screen observation",
                AuraIcons.Vision, HubRoutes.AWARENESS,
            ),
            HubEntry(
                "Proactive", "Unprompted messages",
                AuraIcons.Bolt, HubRoutes.PROACTIVE,
            ),
            HubEntry(
                "Báo thức Aura", "Giờ thức dậy & Morning Briefing",
                AuraIcons.Alarm, HubRoutes.ALARMS,
            ),
            HubEntry(
                "Notifications", "Companion messages",
                AuraIcons.Notifications, HubRoutes.NOTIFICATIONS,
            ),
        ),
    ),
    HubGroup(
        title = "// 03. SYSTEM CAPABILITIES & TOOLS",
        subtitle = "What Aura is allowed to do, and what it reports",
        entries = listOf(
            HubEntry(
                "Agent & Tools", "What Aura may do, and what needs approval",
                AuraIcons.Build, HubRoutes.TOOLS,
            ),
            HubEntry(
                "Sync", "Distributed events, cursors, outbox",
                AuraIcons.Sync, HubRoutes.SYNC,
            ),
            HubEntry(
                "Privacy", "What leaves this phone, and API keys",
                AuraIcons.Shield, HubRoutes.PRIVACY,
            ),
            HubEntry(
                "Diagnostics", "What is reachable, and why not",
                AuraIcons.MonitorHeart, HubRoutes.DIAGNOSTICS,
            ),
        ),
    ),
    HubGroup(
        title = "// 04. NETWORK & DIAGNOSTICS",
        subtitle = null,
        entries = listOf(
            HubEntry(
                "Aura", "Connection, provider, version",
                AuraIcons.Face, HubRoutes.AURA,
            ),
            HubEntry(
                "Connection", "Server URL, token",
                AuraIcons.WifiTethering, HubRoutes.CONNECTION,
            ),
            HubEntry(
                "General", "Appearance, advanced",
                AuraIcons.Tune, HubRoutes.GENERAL,
            ),
        ),
    ),
)

// ----------------------------------------------------------------------
// Shared surfaces
// ----------------------------------------------------------------------

/**
 * The hub's card surface.
 *
 * Reused by every hub screen through [HubSection] - same radius, same tonal
 * step, same hairline edge. The edge is what makes the translucency read as
 * glass rather than as a washed-out rectangle; see
 * `ui/theme/AuraSurfaces.kt`.
 */
@Composable
fun SurfaceCard(modifier: Modifier = Modifier, content: @Composable () -> Unit) {

    val shape = CutCornerShape(topStart = 16.dp, bottomEnd = 16.dp, topEnd = 4.dp, bottomStart = 4.dp)

    Box(
        modifier = modifier
            .fillMaxWidth()
            .clip(shape)
            .border(
                1.dp,
                Brush.horizontalGradient(
                    listOf(
                        Color(0xFF8B5CF6).copy(alpha = 0.35f),
                        Color(0xFF06B6D4).copy(alpha = 0.35f),
                    )
                ),
                shape,
            )
            .auraGlass(shape = shape),
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
    bottomBar: @Composable () -> Unit = {},
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
                        Icon(AuraIcons.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    if (onRefresh != null) {
                        IconButton(onClick = onRefresh) {
                            Icon(
                                AuraIcons.Refresh,
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
        bottomBar = bottomBar,
        containerColor = MaterialTheme.colorScheme.background,
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(auraBackgroundBrush()),
        ) {
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
}

/** Route constants for the hub's navigation graph. */
object HubRoutes {
    const val HUB = "hub"
    const val AURA = "hub/aura"
    const val MODELS = "hub/models"
    const val AWARENESS = "hub/awareness"
    const val MEMORY = "hub/memory"
    const val PROACTIVE = "hub/proactive"
    const val ALARMS = "hub/alarms"
    const val VISION = "hub/vision"
    const val VOICE = "hub/voice"
    const val NOTIFICATIONS = "hub/notifications"
    const val TOOLS = "hub/tools"
    const val PRIVACY = "hub/privacy"
    const val DIAGNOSTICS = "hub/diagnostics"
    const val GENERAL = "hub/general"
    const val CONNECTION = "hub/connection"
    const val SYNC = "hub/sync"
}
