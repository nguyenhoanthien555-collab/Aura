package com.aura.companion.alarm.receiver

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import com.aura.companion.R
import com.aura.companion.alarm.data.AlarmStore
import com.aura.companion.alarm.service.AlarmScheduler
import com.aura.companion.alarm.ui.AuraAlarmActivity

/**
 * BroadcastReceiver triggered by AlarmManager RTC_WAKEUP or device reboot.
 * Dispatches full-screen lockscreen activity and manages notification channel.
 */
class AuraAlarmReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        val action = intent.action ?: return
        Log.i(TAG, "onReceive triggered with action: $action")

        val store = AlarmStore(context)
        val scheduler = AlarmScheduler(context, store)

        when (action) {
            Intent.ACTION_BOOT_COMPLETED -> {
                Log.i(TAG, "Device reboot completed; restoring all active Aura alarms...")
                scheduler.rescheduleAll()
            }

            ACTION_TRIGGER -> {
                val alarmId = intent.getStringExtra(EXTRA_ALARM_ID) ?: ""
                val label = intent.getStringExtra(EXTRA_ALARM_LABEL) ?: "Báo thức Aura"
                triggerAlarmWakeup(context, alarmId, label)
            }

            ACTION_SNOOZE -> {
                val alarmId = intent.getStringExtra(EXTRA_ALARM_ID) ?: ""
                dismissNotification(context, alarmId)
                scheduler.snooze(alarmId, minutes = 5)
            }

            ACTION_DISMISS -> {
                val alarmId = intent.getStringExtra(EXTRA_ALARM_ID) ?: ""
                dismissNotification(context, alarmId)
            }
        }
    }

    private fun triggerAlarmWakeup(context: Context, alarmId: String, label: String) {
        ensureNotificationChannel(context)

        val fullScreenIntent = Intent(context, AuraAlarmActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra(EXTRA_ALARM_ID, alarmId)
            putExtra(EXTRA_ALARM_LABEL, label)
        }
        val fullScreenPendingIntent = PendingIntent.getActivity(
            context,
            alarmId.hashCode(),
            fullScreenIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val snoozeIntent = Intent(context, AuraAlarmReceiver::class.java).apply {
            action = ACTION_SNOOZE
            putExtra(EXTRA_ALARM_ID, alarmId)
        }
        val snoozePendingIntent = PendingIntent.getBroadcast(
            context,
            (alarmId + "_snooze").hashCode(),
            snoozeIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val dismissIntent = Intent(context, AuraAlarmReceiver::class.java).apply {
            action = ACTION_DISMISS
            putExtra(EXTRA_ALARM_ID, alarmId)
        }
        val dismissPendingIntent = PendingIntent.getBroadcast(
            context,
            (alarmId + "_dismiss").hashCode(),
            dismissIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("⏰ $label")
            .setContentText("Aura đang gọi anh thức dậy...")
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setFullScreenIntent(fullScreenPendingIntent, true)
            .setOngoing(true)
            .setAutoCancel(false)
            .addAction(0, "Báo lại 5p", snoozePendingIntent)
            .addAction(0, "Tắt báo thức", dismissPendingIntent)
            .build()

        val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
        notificationManager?.notify(alarmId.hashCode(), notification)

        // Try direct activity launch as well
        try {
            context.startActivity(fullScreenIntent)
        } catch (e: Exception) {
            Log.w(TAG, "Direct activity launch from background prevented; fullScreenIntent will display", e)
        }
    }

    private fun dismissNotification(context: Context, alarmId: String) {
        val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
        notificationManager?.cancel(alarmId.hashCode())
    }

    private fun ensureNotificationChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager
                ?: return

            if (notificationManager.getNotificationChannel(CHANNEL_ID) == null) {
                val channel = NotificationChannel(
                    CHANNEL_ID,
                    "Aura Báo Thức",
                    NotificationManager.IMPORTANCE_HIGH
                ).apply {
                    description = "Thông báo báo thức toàn màn hình chuẩn xác của Aura"
                    setSound(null, null) // Audio handled by AlarmAudioPlayer
                    enableVibration(true)
                    lockscreenVisibility = NotificationCompat.VISIBILITY_PUBLIC
                }
                notificationManager.createNotificationChannel(channel)
            }
        }
    }

    companion object {
        const val TAG = "AuraAlarmReceiver"
        const val CHANNEL_ID = "aura_alarms_channel"

        const val ACTION_TRIGGER = "com.aura.companion.ALARM_TRIGGER"
        const val ACTION_SNOOZE = "com.aura.companion.ALARM_SNOOZE"
        const val ACTION_DISMISS = "com.aura.companion.ALARM_DISMISS"

        const val EXTRA_ALARM_ID = "extra_alarm_id"
        const val EXTRA_ALARM_LABEL = "extra_alarm_label"
    }
}
