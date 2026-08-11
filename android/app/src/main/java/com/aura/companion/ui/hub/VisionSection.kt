package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Computer
import androidx.compose.material.icons.filled.RemoveRedEye
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SelectRow
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.TextEntryDialog
import com.aura.companion.ui.components.ToggleRow

/**
 * Settings → Vision.
 *
 * The cloud/ollama split from Phase 8 is preserved exactly: two separate
 * model names, never one, because the phone-facing caption and the local
 * processor can be configured independently without either misconfiguring
 * the other. The models themselves are free text - the server has no
 * model list for vision, and inventing one here would be guessing.
 *
 * [vision.enabled] gates construction of the remote vision path in
 * `build_services` and takes effect at the next restart; the server says
 * so itself, and the ViewModel surfaces that.
 */
@Composable
fun VisionSection(
    state: HubUiState,
    viewModel: HubViewModel,
    onBack: () -> Unit,
) {
    var editing by remember { mutableStateOf<VisionField?>(null) }

    val vision = state.server.config.vision

    HubSection(
        title = "Vision",
        subtitle = "How Aura reads what it sees",
        onBack = onBack,
        onRefresh = viewModel::refresh,
    ) {

        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        SettingsSection(title = "Vision") {

            ToggleRow(
                title = "Vision",
                subtitle = "Describe screenshots and images. Needs a restart to change.",
                icon = Icons.Filled.RemoveRedEye,
                checked = vision.enabled,
                pending = "vision.enabled" in state.pending,
                lockedReason = state.lockedReason("vision.enabled"),
                onCheckedChange = { viewModel.setFlag("vision.enabled", it) },
            )
        }

        SettingsSection(
            title = "Models",
            subtitle = "Where captions come from, and where they fall back",
        ) {

            SelectRow(
                title = "Cloud model",
                value = vision.cloudModel.ifBlank { "Not set" },
                subtitle = "Used by the server's cloud provider",
                icon = Icons.Filled.Cloud,
                lockedReason = state.lockedReason("vision.cloud_model"),
                onClick = { editing = VisionField.Cloud },
            )

            RowDivider()

            SelectRow(
                title = "Local model",
                value = vision.ollamaModel.ifBlank { "Not set" },
                subtitle = "Used by the on-device processor, where Aura runs",
                icon = Icons.Filled.Computer,
                lockedReason = state.lockedReason("vision.ollama_model"),
                onClick = { editing = VisionField.Local },
            )
        }

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "Vision runs on the Aura server, not on this phone. The " +
                "captions it produces arrive here with the notifications you " +
                "collect.",
            tone = StatusTone.Neutral,
        )

        Spacer(Modifier.height(32.dp))
    }

    // Each model is entered as free text rather than chosen: the server
    // has no list, so a picker would be a list of this app's guesses.
    when (editing) {
        VisionField.Cloud -> TextEntryDialog(
            title = "Cloud model",
            initial = vision.cloudModel,
            label = "Model name",
            help = "The model the server's cloud provider uses to caption " +
                "images. Enter it exactly as the provider spells it.",
            onCommit = { viewModel.setText("vision.cloud_model", it) },
            onDismiss = { editing = null },
        )

        VisionField.Local -> TextEntryDialog(
            title = "Local model",
            initial = vision.ollamaModel,
            label = "Model name",
            help = "The model the local processor uses when no cloud is " +
                "available. Enter it exactly as the provider spells it.",
            onCommit = { viewModel.setText("vision.ollama_model", it) },
            onDismiss = { editing = null },
        )

        null -> Unit
    }
}

private enum class VisionField { Cloud, Local }
