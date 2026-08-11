package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Handyman
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Warning
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.SliderRow
import com.aura.companion.ui.components.StatusRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.ToggleRow
import kotlin.math.roundToInt

/**
 * Settings → Agent & Tools.
 *
 * WHAT THIS SCREEN CAN AND CANNOT CHANGE
 * --------------------------------------
 * Three settings, and they are the three the server actually accepts:
 * whether tools run at all, which risk levels skip the confirmation
 * prompt, and how long one call may take. All three are `ToolPolicy`
 * fields that `tools/executor.py` reads per call, so the server replaces
 * the policy and they take effect on the next tool call - no restart.
 *
 * The rest of the `tools:` section is shown as facts and cannot be edited
 * from here, on purpose. `allowed` decides which tools exist at all, and
 * `allowed_paths` and `applications` decide which filesystem roots and
 * which executables those tools may touch. Those grant a capability
 * rather than configure one, and a bearer token is not enough to hand a
 * remote phone a new verb on somebody's machine. They are edited where
 * Aura is deployed, in `config.yaml`.
 *
 * WHY AUTO-APPROVE IS THREE SWITCHES
 * ----------------------------------
 * Because it is a set, not a level: approving `dangerous` while still
 * confirming `sensitive` is a legitimate configuration, and a single
 * slider from "cautious" to "trusting" could not express it. The last
 * enabled switch locks - `ToolPolicy.from_config` reads
 * `auto_approve or ["safe"]`, so an empty list silently becomes
 * safe-approved, and the server refuses it rather than storing a value
 * the running policy would contradict.
 */
@Composable
fun ToolsSection(
    state: HubUiState,
    viewModel: HubViewModel,
    onBack: () -> Unit,
) {
    val tools = state.server.config.tools

    val approved = tools.autoApprove

    HubSection(
        title = "Agent & Tools",
        subtitle = "What Aura may do, and what needs your approval",
        onBack = onBack,
        onRefresh = viewModel::refresh,
    ) {

        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        SettingsSection(
            title = "Tools",
            subtitle = "Applies to the next tool call, no restart needed",
        ) {

            ToggleRow(
                title = "Allow tools",
                subtitle = "When off, Aura answers but never acts",
                icon = Icons.Filled.Build,
                checked = tools.enabled,
                pending = "tools.enabled" in state.pending,
                lockedReason = state.lockedReason("tools.enabled"),
                onCheckedChange = { viewModel.setFlag("tools.enabled", it) },
            )

            RowDivider()

            SliderRow(
                title = "Time limit",
                subtitle = "How long one tool call may run before Aura gives up",
                value = tools.timeout.toFloat(),
                range = 1f..120f,
                enabled = tools.enabled,
                lockedReason = state.lockedReason("tools.timeout")
                    ?: if (tools.enabled) null else "Allow tools first",
                format = { "${it.roundToInt()}s" },
                onCommit = { viewModel.setNumber("tools.timeout", it.roundToInt()) },
            )
        }

        SettingsSection(
            title = "Approval",
            subtitle = "Levels left off wait for you before the tool runs",
        ) {

            RiskLevel.entries.forEachIndexed { index, level ->

                if (index > 0) RowDivider()

                val on = level.id in approved

                // The last one standing cannot be turned off: the server
                // refuses an empty list, so offering the switch would be
                // offering a guaranteed rejection.
                val lastOne = on && approved.size <= 1

                ToggleRow(
                    title = level.title,
                    subtitle = if (lastOne) {
                        "At least one level must stay approved"
                    } else {
                        level.description
                    },
                    icon = level.icon,
                    checked = on,
                    enabled = tools.enabled,
                    pending = "tools.auto_approve" in state.pending,
                    lockedReason = state.lockedReason("tools.auto_approve")
                        ?: when {
                            !tools.enabled -> "Allow tools first"
                            lastOne -> "At least one level must stay approved"
                            else -> null
                        },
                    onCheckedChange = { wanted ->
                        val next = if (wanted) {
                            approved + level.id
                        } else {
                            approved - level.id
                        }
                        viewModel.setList("tools.auto_approve", next)
                    },
                )
            }
        }

        // ------------------------------------------------------------------
        // Facts. Everything below is read-only and says why.
        // ------------------------------------------------------------------

        SettingsSection(
            title = "What Aura has",
            subtitle = "Set where Aura is deployed, not from this phone",
        ) {

            StatusRow(
                title = "Tools available",
                value = if (tools.allowed.isEmpty()) {
                    "None"
                } else {
                    "${tools.allowed.size}"
                },
                subtitle = tools.allowed
                    .takeIf { it.isNotEmpty() }
                    ?.joinToString(", ")
                    ?: "No tool is permitted, so nothing can run",
                icon = Icons.Filled.Handyman,
                tone = if (tools.allowed.isEmpty()) {
                    StatusTone.Neutral
                } else {
                    StatusTone.Good
                },
            )

            RowDivider()

            StatusRow(
                title = "Folders",
                value = "${tools.allowedPaths.size}",
                subtitle = tools.allowedPaths
                    .takeIf { it.isNotEmpty() }
                    ?.joinToString(", ")
                    ?: "No folder is reachable by a file tool",
                icon = Icons.Filled.Folder,
            )

            RowDivider()

            StatusRow(
                title = "Applications",
                value = "${tools.applications.size}",
                subtitle = tools.applications.keys
                    .takeIf { it.isNotEmpty() }
                    ?.joinToString(", ")
                    ?: "No application can be launched",
                icon = Icons.Filled.Lock,
            )
        }

        if (tools.enabled && tools.allowed.isEmpty()) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = "Tools are allowed but no tool is permitted, so nothing " +
                    "will run. The list is set in Aura's configuration where it " +
                    "is deployed.",
                tone = StatusTone.Warning,
            )
        }

        if ("dangerous" in approved) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = "Aura will change things on its host without asking - " +
                    "launching applications, writing files, clicking. Only leave " +
                    "this on for a machine you are happy for it to drive.",
                tone = StatusTone.Warning,
                icon = Icons.Filled.Warning,
            )
        }

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "Which folders and applications Aura may touch is decided " +
                "in its configuration file, on the machine it runs on. This " +
                "phone can turn tools off and tighten approval, but it cannot " +
                "widen what they reach.",
            tone = StatusTone.Neutral,
        )

        Spacer(Modifier.height(32.dp))
    }
}

/**
 * The three levels, from `ToolRisk` in `tools/base.py`.
 *
 * Ordered least to most consequential, so the list reads as an escalation
 * and the dangerous one is not adjacent to the switch that turns tools on.
 */
private enum class RiskLevel(
    val id: String,
    val title: String,
    val description: String,
    val icon: ImageVector,
) {
    Safe(
        id = "safe",
        title = "Safe actions",
        description = "Reading the time, doing arithmetic - nothing leaves Aura",
        icon = Icons.Filled.Build,
    ),
    Sensitive(
        id = "sensitive",
        title = "Reading your data",
        description = "Opening files and folders on Aura's machine",
        icon = Icons.Filled.Folder,
    ),
    Dangerous(
        id = "dangerous",
        title = "Changing things",
        description = "Launching applications, writing files, clicking",
        icon = Icons.Filled.Warning,
    ),
}
