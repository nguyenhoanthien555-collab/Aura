package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AutoStories
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Storage
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.DangerActionCard
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SectionHeader
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.SliderRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.StepperRow
import com.aura.companion.ui.components.ToggleRow
import kotlin.math.roundToInt

/**
 * Settings → Memory.
 *
 * WHAT IS NOT HERE, AND WHY
 * -------------------------
 * There is no "erase everything Aura remembers" button, because there is
 * no endpoint behind one. Adding a remote route that drops the memory
 * database would be the single most destructive capability in the API, and
 * STEP 9 asks for safe management actions rather than for that. The
 * screen says so plainly instead of offering a button that silently does
 * something smaller than its label.
 *
 * The destructive action that *is* real is reverting these settings to the
 * deployment's own values, and it goes through [DangerActionCard] like
 * anything else that cannot be undone with a tap.
 *
 * RESTART-REQUIRED IS VISIBLE
 * ---------------------------
 * `profile` and `pipeline` gate object construction in `build_services`,
 * so changing them saves immediately and takes effect at the next restart.
 * The ViewModel surfaces the server's own `restart_required` for that;
 * the subtitles here say which rows it will be.
 */
@Composable
fun MemorySection(
    state: HubUiState,
    viewModel: HubViewModel,
    onBack: () -> Unit,
) {
    val memory = state.server.config.memory

    HubSection(
        title = "Memory",
        subtitle = "What Aura keeps and recalls",
        onBack = onBack,
        onRefresh = viewModel::refresh,
    ) {

        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        SettingsSection(title = "Recall") {

            ToggleRow(
                title = "Use memory in replies",
                subtitle = "Look things up from past conversations while answering",
                icon = Icons.Filled.Search,
                checked = memory.recall,
                pending = "memory.recall" in state.pending,
                lockedReason = state.lockedReason("memory.recall"),
                onCheckedChange = { viewModel.setFlag("memory.recall", it) },
            )

            RowDivider()

            ToggleRow(
                title = "Remember new things",
                subtitle = "Extract facts worth keeping. Needs a restart to change.",
                icon = Icons.Filled.AutoStories,
                checked = memory.pipeline,
                pending = "memory.pipeline" in state.pending,
                lockedReason = state.lockedReason("memory.pipeline"),
                onCheckedChange = { viewModel.setFlag("memory.pipeline", it) },
            )

            RowDivider()

            ToggleRow(
                title = "Profile",
                subtitle = "Keep a picture of who you are. Needs a restart to change.",
                icon = Icons.Filled.Person,
                checked = memory.profile,
                pending = "memory.profile" in state.pending,
                lockedReason = state.lockedReason("memory.profile"),
                onCheckedChange = { viewModel.setFlag("memory.profile", it) },
            )
        }

        SettingsSection(
            title = "Limits",
            subtitle = "How much Aura carries into each reply",
        ) {

            StepperRow(
                title = "Conversation history",
                value = memory.historyLimit,
                range = 1..200,
                subtitle = "Recent messages sent with each turn. Needs a restart.",
                lockedReason = state.lockedReason("memory.history_limit"),
                onCommit = { viewModel.setNumber("memory.history_limit", it) },
                format = { "$it" },
            )

            RowDivider()

            // A slider rather than a stepper: 10..5000 by ones is 4,990
            // taps, and the value only matters in the hundreds.
            SliderRow(
                title = "Search depth",
                value = memory.retrievalScope.toFloat(),
                range = 10f..5000f,
                subtitle = "How many stored memories are ranked per lookup",
                lockedReason = state.lockedReason("memory.retrieval_scope"),
                onCommit = {
                    viewModel.setNumber("memory.retrieval_scope", it.roundToInt())
                },
                format = { "${it.roundToInt()}" },
            )
        }

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "Aura's memories live on the server, not on this phone. " +
                "There is no way to erase them from here - do that where Aura " +
                "is deployed.",
            tone = StatusTone.Neutral,
            icon = Icons.Filled.Storage,
        )

        SectionHeader(
            title = "Reset",
            subtitle = "Undo changes made from this app",
        )

        DangerActionCard(
            title = "Revert memory settings",
            description = "Put the five settings above back to the values the " +
                "Aura deployment itself is configured with. Nothing Aura " +
                "remembers is deleted.",
            actionLabel = "Revert",
            confirmBody = "Recall, remembering, profile, history and search " +
                "depth will go back to the server's own configuration. Some of " +
                "them need an Aura restart to take effect.",
            enabled = state.connected,
            busy = state.loading,
            onConfirm = { viewModel.resetSettings(MEMORY_PATHS) },
        )

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "History and search depth are read when Aura starts, so a " +
                "change to either is saved now and applied at the next restart.",
            tone = StatusTone.Neutral,
            icon = Icons.Filled.History,
        )

        Spacer(Modifier.height(32.dp))
    }
}

/** The dotted paths this screen owns, for a scoped reset. */
private val MEMORY_PATHS = listOf(
    "memory.recall",
    "memory.profile",
    "memory.pipeline",
    "memory.history_limit",
    "memory.retrieval_scope",
)
