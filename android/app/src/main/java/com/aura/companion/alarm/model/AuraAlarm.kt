package com.aura.companion.alarm.model

import kotlinx.serialization.Serializable
import java.util.UUID

/**
 * Domain model representing a configured alarm in the Aura system.
 */
@Serializable
data class AuraAlarm(
    val id: String = UUID.randomUUID().toString(),
    val hour: Int,
    val minute: Int,
    val label: String = "Báo thức Aura",
    val isEnabled: Boolean = true,
    val repeatDays: List<Int> = emptyList(), // 1=Mon .. 7=Sun; empty list represents one-shot alarm
    val soundType: String = SOUND_AURA_VOICE_SYNTH,
    val createdAt: Long = System.currentTimeMillis()
) {
    companion object {
        const val SOUND_AURA_VOICE_SYNTH = "aura_voice_synth"
        const val SOUND_SYSTEM_DEFAULT = "system_default"
    }

    val formattedTime: String
        get() = String.format("%02d:%02d", hour, minute)
}
