package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.RecordVoiceOver
import androidx.compose.material.icons.filled.Speaker
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.StatusRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.ToggleRow

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
 * WHY THERE IS NO VOICE PICKER
 * ----------------------------
 * `voice.tts.enabled` and `voice.stt.enabled` are the only two voice keys
 * in the server's allow-list. Provider, rate, wake word and record length
 * are read at construction from that host's config file, and offering a
 * control that PATCHes a path the server rejects is exactly the fake
 * toggle STEP 7 forbids. The provider in use is shown as a fact instead.
 */
@Composable
fun VoiceSection(
    state: HubUiState,
    viewModel: HubViewModel,
    onBack: () -> Unit,
) {
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

            StatusRow(
                title = "Engine",
                value = voice.tts.provider.ifBlank { "—" },
                subtitle = "Chosen where Aura is deployed",
                icon = Icons.Filled.Speaker,
                tone = if (voice.tts.enabled) StatusTone.Good else StatusTone.Neutral,
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
}
