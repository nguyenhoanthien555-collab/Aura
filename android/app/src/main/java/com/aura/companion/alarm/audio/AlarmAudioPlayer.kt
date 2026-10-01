package com.aura.companion.alarm.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.MediaPlayer
import android.media.RingtoneManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import android.util.Log

/**
 * Controller for multi-stage alarm audio playback and haptics.
 * Implements Stage 1 (gentle wake) -> Stage 2 (escalated urgent wake) escalation.
 */
class AlarmAudioPlayer(private val context: Context) {

    private var mediaPlayer: MediaPlayer? = null
    private var vibrator: Vibrator? = null
    private val handler = Handler(Looper.getMainLooper())
    private val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager

    private var originalVolume: Int = -1
    private var currentStage: Int = 1
    private var isPlaying: Boolean = false

    private val stage2EscalationRunnable = Runnable {
        escalateToStage2()
    }

    init {
        vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val vm = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
            vm?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }
    }

    /**
     * Start alarm playback at Stage 1 with automatic 3-minute escalation to Stage 2.
     */
    fun start(stage1TimeoutSeconds: Int = 180) {
        if (isPlaying) return
        isPlaying = true
        currentStage = 1

        val maxVol = audioManager?.getStreamMaxVolume(AudioManager.STREAM_ALARM) ?: 15
        originalVolume = audioManager?.getStreamVolume(AudioManager.STREAM_ALARM) ?: (maxVol / 2)

        // Stage 1: Soft start (approx 45% volume)
        val stage1Vol = (maxVol * 0.45f).toInt().coerceAtLeast(1)
        audioManager?.setStreamVolume(AudioManager.STREAM_ALARM, stage1Vol, 0)

        playAlarmAudio()
        startVibration(stage = 1)

        // Schedule escalation to Stage 2
        handler.removeCallbacks(stage2EscalationRunnable)
        handler.postDelayed(stage2EscalationRunnable, stage1TimeoutSeconds * 1000L)
        Log.i(TAG, "Started Stage 1 alarm playback. Escalation scheduled in $stage1TimeoutSeconds s.")
    }

    /**
     * Escalates alarm to Stage 2: full volume, persistent urgent vibration.
     */
    fun escalateToStage2() {
        if (!isPlaying || currentStage == 2) return
        currentStage = 2

        val maxVol = audioManager?.getStreamMaxVolume(AudioManager.STREAM_ALARM) ?: 15
        audioManager?.setStreamVolume(AudioManager.STREAM_ALARM, maxVol, 0)

        startVibration(stage = 2)
        Log.w(TAG, "Escalated to Stage 2: Maximum volume and urgent alert vibration.")
    }

    private fun playAlarmAudio() {
        try {
            var alarmUri: Uri? = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM)
            if (alarmUri == null) {
                alarmUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
            }

            mediaPlayer = MediaPlayer().apply {
                setDataSource(context, alarmUri!!)
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ALARM)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build()
                )
                isLooping = true
                prepare()
                start()
            }
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start MediaPlayer for alarm", e)
        }
    }

    private fun startVibration(stage: Int) {
        vibrator?.let { vib ->
            if (!vib.hasVibrator()) return@let

            val pattern = if (stage == 1) {
                // Gentle pulse: 0ms wait, 500ms vibrate, 1000ms pause
                longArrayOf(0, 500, 1000, 500, 1000)
            } else {
                // Urgent alert: rapid aggressive vibration
                longArrayOf(0, 800, 300, 800, 300, 1000, 200)
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                val effect = VibrationEffect.createWaveform(pattern, 0)
                vib.vibrate(effect)
            } else {
                @Suppress("DEPRECATION")
                vib.vibrate(pattern, 0)
            }
        }
    }

    /**
     * Stop all audio and haptics immediately.
     */
    fun stop() {
        if (!isPlaying) return
        isPlaying = false
        handler.removeCallbacks(stage2EscalationRunnable)

        try {
            mediaPlayer?.let { mp ->
                if (mp.isPlaying) mp.stop()
                mp.release()
            }
        } catch (e: Exception) {
            Log.w(TAG, "Exception stopping media player: ${e.message}")
        } finally {
            mediaPlayer = null
        }

        try {
            vibrator?.cancel()
        } catch (_: Exception) {}

        // Restore original volume if known
        if (originalVolume >= 0) {
            audioManager?.setStreamVolume(AudioManager.STREAM_ALARM, originalVolume, 0)
        }

        Log.i(TAG, "AlarmAudioPlayer stopped cleanly.")
    }

    companion object {
        private const val TAG = "AlarmAudioPlayer"
    }
}
