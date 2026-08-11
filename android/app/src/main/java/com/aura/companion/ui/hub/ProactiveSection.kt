package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bedtime
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.NotificationsActive
import androidx.compose.material.icons.filled.Timer
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.ChoiceDialog
import com.aura.companion.ui.components.DangerActionCard
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SectionHeader
import com.aura.companion.ui.components.SelectRow
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.SliderRow
import com.aura.companion.ui.components.StatusRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.StepperRow
import com.aura.companion.ui.components.ToggleRow
import kotlin.math.roundToInt

/**
 * Settings → Proactive.
 *
 * TWO DIFFERENT QUESTIONS
 * -----------------------
 * STEP 10 requires this screen to distinguish "the proactive engine is
 * enabled" from "a message can actually reach you", and they are not the
 * same thing at all:
 *
 *   The engine decides whether to speak, and only gets the chance while
 *   something polls `/api/notifications` - there is no background
 *   scheduler on the server.
 *
 *   Delivery to this phone is WorkManager polling every fifteen minutes,
 *   which is both the transport and the engine's only heartbeat while the
 *   app is closed. Turn phone notifications off and the engine mostly
 *   stops being asked, not just muted.
 *
 * The status rows say that in as many words. A screen that showed one
 * switch would be claiming a background push system that does not exist.
 *
 * THE DEFAULTS STAY CONSERVATIVE
 * ------------------------------
 * The bounds here are the server's: 5 minutes to 24 hours of cooldown, at
 * most 20 messages a day, similarity never below 0.1. Nothing on this
 * screen can turn the anti-spam gate off, because nothing on the server
 * can either.
 */
@Composable
fun ProactiveSection(
    state: HubUiState,
    viewModel: HubViewModel,
    onBack: () -> Unit,
) {
    var editingQuietHours by remember { mutableStateOf(false) }

    val proactive = state.server.config.proactive

    HubSection(
        title = "Proactive",
        subtitle = "When Aura speaks first",
        onBack = onBack,
        onRefresh = viewModel::refresh,
    ) {

        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        SettingsSection(title = "Proactive messages") {

            ToggleRow(
                title = "Let Aura start conversations",
                subtitle = "Reminders, follow-ups and greetings it decides to send",
                icon = Icons.Filled.Bolt,
                checked = proactive.enabled,
                pending = "proactive.enabled" in state.pending,
                lockedReason = state.lockedReason("proactive.enabled"),
                onCheckedChange = { viewModel.setFlag("proactive.enabled", it) },
            )
        }

        // ------------------------------------------------------------------
        // Delivery - the honest part
        // ------------------------------------------------------------------

        SettingsSection(
            title = "Delivery",
            subtitle = "How a proactive message reaches this phone",
        ) {

            StatusRow(
                title = "Engine",
                value = if (proactive.enabled) "Enabled" else "Off",
                tone = if (proactive.enabled) StatusTone.Good else StatusTone.Neutral,
                icon = Icons.Filled.Bolt,
            )

            RowDivider()

            StatusRow(
                title = "Delivery to this phone",
                value = when {
                    !state.device.notificationsEnabled -> "Off"
                    !state.device.isConfigured -> "Not connected"
                    else -> "Every 15 minutes"
                },
                tone = when {
                    !state.device.notificationsEnabled -> StatusTone.Neutral
                    !state.device.isConfigured -> StatusTone.Warning
                    else -> StatusTone.Good
                },
                icon = Icons.Filled.NotificationsActive,
            )
        }

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "Aura has no background scheduler of its own. It gets the " +
                "chance to speak when this app checks in, which is roughly " +
                "every fifteen minutes while notifications are on - so a " +
                "proactive message can arrive late, and none arrive at all " +
                "when notifications are off.",
            tone = StatusTone.Neutral,
            icon = Icons.Filled.Timer,
        )

        if (proactive.enabled && !state.device.notificationsEnabled) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = "Proactive messages are on, but this phone has " +
                    "notifications turned off, so nothing will be delivered " +
                    "here. Turn them on under Notifications.",
                tone = StatusTone.Warning,
            )
        }

        // ------------------------------------------------------------------
        // Frequency
        // ------------------------------------------------------------------

        SettingsSection(
            title = "Frequency",
            subtitle = "Limits Aura will not exceed",
        ) {

            SliderRow(
                title = "Minimum gap",
                value = proactive.cooldownSeconds.toFloat(),
                range = 300f..86_400f,
                subtitle = "Time between one message and the next",
                enabled = proactive.enabled,
                lockedReason = state.lockedReason("proactive.cooldown_seconds"),
                onCommit = {
                    viewModel.setNumber(
                        "proactive.cooldown_seconds",
                        it.roundToInt(),
                    )
                },
                format = { formatDuration(it.roundToInt()) },
            )

            RowDivider()

            StepperRow(
                title = "Most per day",
                value = proactive.maxPerDay,
                range = 1..20,
                subtitle = "A hard ceiling, counted per calendar day",
                enabled = proactive.enabled,
                lockedReason = state.lockedReason("proactive.max_per_day"),
                onCommit = { viewModel.setNumber("proactive.max_per_day", it) },
            )

            RowDivider()

            SelectRow(
                title = "Quiet hours",
                value = proactive.quietHours.describe(),
                subtitle = "Aura stays silent during these",
                icon = Icons.Filled.Bedtime,
                enabled = proactive.enabled,
                lockedReason = state.lockedReason("proactive.quiet_hours"),
                onClick = { editingQuietHours = true },
            )
        }

        // ------------------------------------------------------------------
        // Repetition
        // ------------------------------------------------------------------

        SettingsSection(
            title = "Repetition",
            subtitle = "How hard Aura tries not to repeat itself",
        ) {

            SliderRow(
                title = "Similarity threshold",
                value = proactive.similarityThreshold.toFloat(),
                range = 0.1f..1f,
                steps = 17,
                subtitle = "Lower means more messages are judged too similar",
                enabled = proactive.enabled,
                lockedReason = state.lockedReason("proactive.similarity_threshold"),
                onCommit = {
                    viewModel.setNumber(
                        "proactive.similarity_threshold",
                        (it * 100).roundToInt() / 100.0,
                    )
                },
                format = { "%.2f".format(it) },
            )

            RowDivider()

            SliderRow(
                title = "Look back over",
                value = proactive.duplicateWindowSeconds.toFloat(),
                range = 60f..604_800f,
                subtitle = "How far back Aura checks before repeating itself",
                enabled = proactive.enabled,
                lockedReason = state.lockedReason("proactive.duplicate_window_seconds"),
                onCommit = {
                    viewModel.setNumber(
                        "proactive.duplicate_window_seconds",
                        it.roundToInt(),
                    )
                },
                format = { formatDuration(it.roundToInt()) },
            )
        }

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "These limits are floors as well as settings: Aura cannot be " +
                "configured to send more than 20 messages a day, or to check " +
                "less than five minutes apart.",
            tone = StatusTone.Neutral,
            icon = Icons.Filled.ContentCopy,
        )

        SectionHeader(title = "Reset")

        DangerActionCard(
            title = "Revert proactive settings",
            description = "Put every setting on this screen back to the values " +
                "the Aura deployment is configured with.",
            actionLabel = "Revert",
            confirmBody = "Frequency, quiet hours and repetition settings will " +
                "go back to the server's own configuration.",
            enabled = state.connected,
            busy = state.loading,
            onConfirm = { viewModel.resetSettings(PROACTIVE_PATHS) },
        )

        Spacer(Modifier.height(32.dp))
    }

    if (editingQuietHours) {
        QuietHoursDialog(
            windows = proactive.quietHours,
            onCommit = viewModel::setQuietHours,
            onDismiss = { editingQuietHours = false },
        )
    }
}

/**
 * Quiet hours, as one window.
 *
 * The server accepts up to four windows and handles one that wraps
 * midnight. This edits the first, which is what people actually want ("not
 * between 10pm and 8am"), and says so rather than pretending the extra
 * three do not exist. Anything more elaborate is set where Aura is
 * deployed.
 */
@Composable
private fun QuietHoursDialog(
    windows: List<List<Int>>,
    onCommit: (List<List<Int>>) -> Unit,
    onDismiss: () -> Unit,
) {
    val first = windows.firstOrNull()

    var start by remember { mutableIntStateOf(first?.getOrNull(0) ?: 22) }

    var pickingEnd by remember { mutableStateOf(false) }

    if (!pickingEnd) {
        ChoiceDialog(
            title = "Quiet from",
            options = HOURS,
            selected = hourLabel(start),
            onPick = { picked ->
                start = HOURS.indexOf(picked)
                pickingEnd = true
            },
            // [ChoiceDialog] dismisses itself after a pick, which would
            // close this whole flow before the second question is asked.
            // The guard distinguishes "chose an hour" from "tapped away":
            // the pick has already set `pickingEnd`, a cancel has not.
            onDismiss = { if (!pickingEnd) onDismiss() },
        )
    } else {
        ChoiceDialog(
            title = "Quiet until",
            options = HOURS,
            selected = hourLabel(first?.getOrNull(1) ?: 8),
            onPick = { picked ->
                val end = HOURS.indexOf(picked)
                // Replace only the first window; anything the deployment
                // configured beyond it is left alone.
                onCommit(listOf(listOf(start, end)) + windows.drop(1))
            },
            onDismiss = onDismiss,
        )
    }
}

private val HOURS: List<String> = (0..23).map { hourLabel(it) }

private fun hourLabel(hour: Int): String = "%02d:00".format(hour.coerceIn(0, 23))

/** "None", or "22:00 - 08:00", or "2 windows". */
private fun List<List<Int>>.describe(): String = when {
    isEmpty() -> "None"
    size == 1 -> {
        val window = first()
        val from = window.getOrNull(0) ?: 0
        val until = window.getOrNull(1) ?: 0
        "${hourLabel(from)} - ${hourLabel(until)}"
    }
    else -> "$size windows"
}

/** Seconds as something a person reads: "2 hours", "45 minutes". */
private fun formatDuration(seconds: Int): String = when {
    seconds < 60 -> "${seconds}s"
    seconds < 3600 -> "${seconds / 60} min"
    seconds < 86_400 -> {
        val hours = seconds / 3600
        if (hours == 1) "1 hour" else "$hours hours"
    }
    else -> {
        val days = seconds / 86_400
        if (days == 1) "1 day" else "$days days"
    }
}

private val PROACTIVE_PATHS = listOf(
    "proactive.enabled",
    "proactive.cooldown_seconds",
    "proactive.max_per_day",
    "proactive.quiet_hours",
    "proactive.duplicate_window_seconds",
    "proactive.similarity_threshold",
)
