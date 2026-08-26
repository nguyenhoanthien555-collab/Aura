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
import androidx.compose.foundation.clickable
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
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
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
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    IconButton(onClick = viewModel::refresh) {
                        Icon(
                            Icons.Filled.Refresh,
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
                        reduced = reduced,
                    )
                }

                item(key = "banner") {
                    AnimatedVisibility(
                        visible = banner != null,
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

                item(key = "tiles") {
                    TileGrid(
                        tiles = hubTiles(state),
                        reduced = reduced,
                        onOpen = onOpenSection,
                    )
                }

                item(key = "chat") {
                    ChatCard(onClick = onOpenChat)
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
 * WHY THIS READS `reach` AND NOT `loaded`
 * ---------------------------------------
 * It used to say "Disconnected" whenever `GET /api/settings` failed, which
 * on a deployment predating the Control Hub API meant the headline
 * contradicted the chat tab working in the background. The verdict comes
 * from [hubHeadline] now, which anchors on the health rung; a missing
 * settings API is a second line, not a different verdict.
 */
@Composable
private fun HeroCard(
    headline: HubHeadline,
    version: String,
    reduced: Boolean,
) {
    val shape = RoundedCornerShape(28.dp)

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .auraGlassBlur(
                shape = shape,
                tint = MaterialTheme.colorScheme.primary.copy(alpha = 0.12f)
            )
            .background(brush = auraHeroBrush(), shape = shape, alpha = 0.5f),
    ) {
        Row(
            modifier = Modifier.padding(22.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {

            Column(modifier = Modifier.weight(1f)) {

                Text(
                    text = "Aura",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                )

                Spacer(Modifier.height(6.dp))

                Text(
                    text = headline.title,
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.SemiBold,
                )

                Spacer(Modifier.height(6.dp))

                Text(
                    text = headline.detail,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                if (version.isNotBlank()) {
                    Spacer(Modifier.height(12.dp))
                    Text(
                        text = "Aura $version",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant
                            .copy(alpha = 0.7f),
                    )
                }
            }

            Spacer(Modifier.width(16.dp))

            StatusRing(tone = headline.tone, busy = headline.busy, reduced = reduced)
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
// The glance
// ----------------------------------------------------------------------

/** Two rows of two, because four tiles across a phone is four illegible tiles. */
@Composable
private fun TileGrid(
    tiles: List<HubTile>,
    reduced: Boolean,
    onOpen: (String) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {

        tiles.chunked(2).forEach { pair ->

            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {

                pair.forEach { tile ->
                    StatusTile(
                        tile = tile,
                        reduced = reduced,
                        onClick = { onOpen(tile.route) },
                        modifier = Modifier.weight(1f),
                    )
                }

                // An odd number of tiles must not stretch the last one to
                // double width; the layout is a grid, not a flow.
                if (pair.size == 1) Spacer(Modifier.weight(1f))
            }
        }
    }
}

/**
 * One compact status tile.
 *
 * Tappable, and it opens the section that owns the value it displays -
 * because the point of showing "Awareness · Watching" on the front page is
 * that the user can do something about it in one more tap.
 */
@Composable
private fun StatusTile(
    tile: HubTile,
    reduced: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val shape = RoundedCornerShape(18.dp)

    val colour = animateColorAsState(
        targetValue = tile.tone.contentColour(),
        animationSpec = tween(AuraMotion.scaled(AuraMotion.Standard, reduced)),
        label = "tileTone",
    ).value

    Box(
        modifier = modifier
            .auraGlassBlur(
                shape = shape,
                tint = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.2f)
            )
            .background(brush = auraTileBrush(), shape = shape, alpha = 0.6f)
            .clickable(onClick = onClick),
    ) {
        Column(modifier = Modifier.padding(14.dp)) {

            Row(verticalAlignment = Alignment.CenterVertically) {

                Icon(
                    imageVector = tile.kind.icon(),
                    contentDescription = null,
                    tint = colour,
                    modifier = Modifier.size(16.dp),
                )

                Spacer(Modifier.width(8.dp))

                Text(
                    text = tile.label,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Spacer(Modifier.height(8.dp))

            Text(
                text = tile.value,
                style = MaterialTheme.typography.titleMedium,
                color = colour,
            )
        }
    }
}

private fun HubTileKind.icon(): ImageVector = when (this) {
    HubTileKind.Provider -> Icons.Filled.Psychology
    HubTileKind.Memory -> Icons.Filled.Memory
    HubTileKind.Awareness -> Icons.Filled.Visibility
    HubTileKind.Proactive -> Icons.Filled.Bolt
}

/** Chat, given the weight it deserves: the reason the app exists. */
@Composable
private fun ChatCard(onClick: () -> Unit) {

    val shape = RoundedCornerShape(28.dp)

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .auraGlassBlur(
                shape = shape,
                tint = MaterialTheme.colorScheme.primary.copy(alpha = 0.16f)
            )
            .clickable(onClick = onClick),
    ) {
        Row(
            modifier = Modifier.padding(18.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {

            Icon(
                Icons.Filled.ChatBubbleOutline,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
            )

            Spacer(Modifier.width(14.dp))

            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "Talk to Aura",
                    style = MaterialTheme.typography.titleMedium,
                )
                Text(
                    text = "Conversation, streaming replies",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
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
        title = "Intelligence",
        subtitle = "How Aura thinks and what it remembers",
        entries = listOf(
            HubEntry(
                "AI & Models", "Provider, model, API keys",
                Icons.Filled.Psychology, HubRoutes.MODELS,
            ),
            HubEntry(
                "Memory", "Recall, profile, history",
                Icons.Filled.Memory, HubRoutes.MEMORY,
            ),
            HubEntry(
                "Vision", "Image understanding",
                Icons.Filled.RemoveRedEye, HubRoutes.VISION,
            ),
            HubEntry(
                "Voice", "Text to speech, speech to text",
                Icons.AutoMirrored.Filled.VolumeUp, HubRoutes.VOICE,
            ),
        ),
    ),
    HubGroup(
        title = "Presence",
        subtitle = "What Aura may see, and when it may speak first",
        entries = listOf(
            HubEntry(
                "Awareness", "Screen observation",
                Icons.Filled.Visibility, HubRoutes.AWARENESS,
            ),
            HubEntry(
                "Proactive", "Unprompted messages",
                Icons.Filled.Bolt, HubRoutes.PROACTIVE,
            ),
            HubEntry(
                "Notifications", "Companion messages",
                Icons.Filled.Notifications, HubRoutes.NOTIFICATIONS,
            ),
        ),
    ),
    HubGroup(
        title = "Control",
        subtitle = "What Aura is allowed to do, and what it reports",
        entries = listOf(
            HubEntry(
                "Agent & Tools", "What Aura may do, and what needs approval",
                Icons.Filled.Build, HubRoutes.TOOLS,
            ),
            HubEntry(
                "Privacy", "What leaves this phone, and API keys",
                Icons.Filled.Shield, HubRoutes.PRIVACY,
            ),
            HubEntry(
                "Diagnostics", "What is reachable, and why not",
                Icons.Filled.MonitorHeart, HubRoutes.DIAGNOSTICS,
            ),
        ),
    ),
    HubGroup(
        title = "Server & app",
        subtitle = null,
        entries = listOf(
            HubEntry(
                "Aura", "Connection, provider, version",
                Icons.Filled.Face, HubRoutes.AURA,
            ),
            HubEntry(
                "Connection", "Server URL, token",
                Icons.Filled.WifiTethering, HubRoutes.CONNECTION,
            ),
            HubEntry(
                "General", "Appearance, advanced",
                Icons.Filled.Tune, HubRoutes.GENERAL,
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

    val shape = RoundedCornerShape(20.dp)

    Box(
        modifier = modifier
            .fillMaxWidth()
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
    const val VISION = "hub/vision"
    const val VOICE = "hub/voice"
    const val NOTIFICATIONS = "hub/notifications"
    const val TOOLS = "hub/tools"
    const val PRIVACY = "hub/privacy"
    const val DIAGNOSTICS = "hub/diagnostics"
    const val GENERAL = "hub/general"
    const val CONNECTION = "hub/connection"
}
