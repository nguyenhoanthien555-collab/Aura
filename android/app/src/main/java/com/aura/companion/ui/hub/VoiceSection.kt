package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material.icons.filled.Speaker
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.ChoiceDialog
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SelectRow
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.SliderRow
import com.aura.companion.ui.components.StatusRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.TextEntryDialog
import com.aura.companion.ui.components.ToggleRow
import kotlin.math.roundToInt

/**
 * Settings → Voice.
 *
 * WHERE THE VOICE ACTUALLY IS
 * ---------------------------
 * On the machine running Aura, not on this phone. `launcher/services.py`
 * builds `TTSEngine` and `SpeechToTextEngine` against that host's speakers
 * and microphone; this app has no TTS or STT of its own. So a phone on the
 * far side of a Render deployment can turn voice on and hear nothing -
 * which is not a bug, and is the first thing this screen says.
 *
 * WHAT IS SETTABLE, AND WHAT MOVES WITHOUT A RESTART
 * --------------------------------------------------
 * `voice` and `volume` are passed through at every synthesis, so the server
 * can move them on a live engine and the next reply uses them. `provider`
 * selects the class and `playback` is `create_audio_player(enabled=...)`,
 * both decided when the engine was built - saved immediately, in effect
 * after a restart, and the rows say so.
 *
 * `rate` and `pitch` are deliberately absent. The mood pacing system owns
 * them at runtime and restores from its own baseline, so a value set here
 * would be reverted by the next mood change - a control that un-sets itself
 * is worse than no control. STT has only its switch: model, language, wake
 * word and record length are read at construction from the host's config.
 */
@Composable
fun VoiceSection(
    state: HubUiState,
    viewModel: HubViewModel,
    onBack: () -> Unit,
) {
    var editing by remember { mutableStateOf<VoiceField?>(null) }

    val voice = state.server.config.voice

    HubSection(
        title = "Voice",
        subtitle = "Speaking and listening, on the Aura host",
        onBack = onBack,
        onRefresh = viewModel::refresh,
    ) {

        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        NoticeCard(
            text = "Voice runs where Aura runs. If your Aura is deployed to a " +
                "server, turning these on will not make this phone speak or " +
                "listen - there is no phone-side voice in this app.",
            tone = StatusTone.Neutral,
            icon = Icons.Filled.RecordVoiceOver,
        )

        Spacer(Modifier.height(4.dp))

        SettingsSection(
            title = "Speech output",
            subtitle = "Aura reading its replies aloud",
        ) {

            ToggleRow(
                title = "Text to speech",
                subtitle = "Needs a restart of Aura to change",
                icon = Icons.AutoMirrored.Filled.VolumeUp,
                checked = voice.tts.enabled,
                pending = "voice.tts.enabled" in state.pending,
                lockedReason = state.lockedReason("voice.tts.enabled"),
                onCheckedChange = { viewModel.setFlag("voice.tts.enabled", it) },
            )

            RowDivider()

            SelectRow(
                title = "Engine",
                value = voice.tts.provider.ifBlank { "auto" },
                subtitle = "Which speech engine to build. Needs a restart.",
                icon = Icons.Filled.Speaker,
                lockedReason = state.lockedReason("voice.tts.provider"),
                onClick = { editing = VoiceField.Engine },
            )

            RowDivider()

            SelectRow(
                title = "Voice",
                value = voice.tts.voice.ifBlank { "Engine default" },
                subtitle = "Takes effect on the next reply Aura speaks",
                icon = Icons.Filled.RecordVoiceOver,
                lockedReason = state.lockedReason("voice.tts.voice"),
                onClick = { editing = VoiceField.Name },
            )

            RowDivider()

            SliderRow(
                title = "Volume",
                subtitle = "Applies from the next reply",
                value = voice.tts.volume.toFloat(),
                range = 0f..100f,
                steps = 19,
                lockedReason = state.lockedReason("voice.tts.volume"),
                format = { "${it.roundToInt()}%" },
                onCommit = { viewModel.setNumber("voice.tts.volume", it.roundToInt()) },
            )

            RowDivider()

            ToggleRow(
                title = "Play audio",
                subtitle = "Off synthesises silently. Needs a restart to change.",
                icon = Icons.AutoMirrored.Filled.VolumeUp,
                checked = voice.tts.playback,
                pending = "voice.tts.playback" in state.pending,
                lockedReason = state.lockedReason("voice.tts.playback"),
                onCheckedChange = { viewModel.setFlag("voice.tts.playback", it) },
            )
        }

        SettingsSection(
            title = "Speech input",
            subtitle = "Aura listening through the host's microphone",
        ) {

            ToggleRow(
                title = "Speech to text",
                subtitle = "Needs a restart of Aura to change",
                icon = Icons.Filled.Mic,
                checked = voice.stt.enabled,
                pending = "voice.stt.enabled" in state.pending,
                lockedReason = state.lockedReason("voice.stt.enabled"),
                onCheckedChange = { viewModel.setFlag("voice.stt.enabled", it) },
            )

            RowDivider()

            StatusRow(
                title = "Engine",
                value = voice.stt.provider.ifBlank { "—" },
                subtitle = "Chosen where Aura is deployed",
                icon = Icons.Filled.RecordVoiceOver,
                tone = if (voice.stt.enabled) StatusTone.Good else StatusTone.Neutral,
            )
        }

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "Aura falls back to a silent mock when a host has no speakers, " +
                "no microphone or no model, so these can be on without " +
                "anything breaking. The engine shown above is what it settled " +
                "on at startup.",
            tone = StatusTone.Neutral,
        )

        Spacer(Modifier.height(32.dp))
    }

    when (editing) {

        // A real list, from `_TTS_BUILDERS` in `voice/factory.py`, with the
        // aliases collapsed. "auto" is first because it is the default and
        // the only one guaranteed to work on any host.
        VoiceField.Engine -> ChoiceDialog(
            title = "Speech engine",
            options = listOf("auto", "edge", "sapi", "pyttsx3", "mock"),
            selected = voice.tts.provider.ifBlank { "auto" },
            labels = { name ->
                when (name) {
                    "auto" -> "Automatic - best available on the host"
                    "edge" -> "Edge - Aura's own voice, needs a network call"
                    "sapi" -> "Windows SAPI"
                    "pyttsx3" -> "pyttsx3"
                    else -> "Silent mock"
                }
            },
            onPick = { viewModel.setText("voice.tts.provider", it) },
            onDismiss = { editing = null },
        )

        // Free text: the voice names belong to whichever engine is built,
        // and this app cannot enumerate a host's installed voices.
        VoiceField.Name -> TextEntryDialog(
            title = "Voice",
            initial = voice.tts.voice,
            label = "Voice name",
            help = "The engine's own name for the voice - for Edge, something " +
                "like en-GB-SoniaNeural. Leave it empty for the engine's default.",
            onCommit = { viewModel.setText("voice.tts.voice", it) },
            onDismiss = { editing = null },
        )

        null -> Unit
    }
}

private enum class VoiceField { Engine, Name }
