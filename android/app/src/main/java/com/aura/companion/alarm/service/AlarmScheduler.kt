package com.aura.companion.alarm.service

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.util.Log
import com.aura.companion.MainActivity
import com.aura.companion.alarm.data.IAlarmStore
import com.aura.companion.alarm.model.AuraAlarm
import com.aura.companion.alarm.receiver.AuraAlarmReceiver

/**
 * Service orchestrating system AlarmManager scheduling.
 * Employs setAlarmClock for highest reliability across Android Deep Doze and ColorOS battery savers.
 */
class AlarmScheduler(
    private val context: Context,
    private val store: IAlarmStore
) {
    private val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as? AlarmManager

    fun schedule(alarm: AuraAlarm): Long {
        if (!alarm.isEnabled) {
            cancel(alarm.id)
            return -1L
        }

        val triggerMillis = store.calculateNextTriggerMillis(alarm)
        if (alarmManager == null) {
            Log.e(TAG, "AlarmManager unavailable on this device")
            return triggerMillis
        }

        val showIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val showPendingIntent = PendingIntent.getActivity(
            context,
            alarm.id.hashCode(),
            showIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val triggerIntent = Intent(context, AuraAlarmReceiver::class.java).apply {
            action = AuraAlarmReceiver.ACTION_TRIGGER
            putExtra(AuraAlarmReceiver.EXTRA_ALARM_ID, alarm.id)
            putExtra(AuraAlarmReceiver.EXTRA_ALARM_LABEL, alarm.label)
        }
        val triggerPendingIntent = PendingIntent.getBroadcast(
            context,
            alarm.id.hashCode(),
            triggerIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        try {
            val clockInfo = AlarmManager.AlarmClockInfo(triggerMillis, showPendingIntent)
            alarmManager.setAlarmClock(clockInfo, triggerPendingIntent)
            Log.i(TAG, "Scheduled alarm '${alarm.label}' (id=${alarm.id}) for epoch $triggerMillis (${alarm.formattedTime})")
        } catch (e: SecurityException) {
            Log.w(TAG, "Failed setAlarmClock due to exact alarm restrictions; falling back to setExactAndAllowWhileIdle", e)
            alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerMillis, triggerPendingIntent)
        }

        return triggerMillis
    }

    fun snooze(alarmId: String, minutes: Int = 5): Long {
        val alarm = store.get(alarmId) ?: AuraAlarm(id = alarmId, hour = 0, minute = 0, label = "Báo lại")
        val triggerMillis = System.currentTimeMillis() + (minutes * 60 * 1000L)

        if (alarmManager == null) return triggerMillis

        val triggerIntent = Intent(context, AuraAlarmReceiver::class.java).apply {
            action = AuraAlarmReceiver.ACTION_TRIGGER
            putExtra(AuraAlarmReceiver.EXTRA_ALARM_ID, alarm.id)
            putExtra(AuraAlarmReceiver.EXTRA_ALARM_LABEL, "${alarm.label} (Báo lại)")
        }
        val triggerPendingIntent = PendingIntent.getBroadcast(
            context,
            alarm.id.hashCode(),
            triggerIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val showIntent = Intent(context, MainActivity::class.java)
        val showPendingIntent = PendingIntent.getActivity(
            context,
            alarm.id.hashCode(),
            showIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        try {
            alarmManager.setAlarmClock(AlarmManager.AlarmClockInfo(triggerMillis, showPendingIntent), triggerPendingIntent)
        } catch (_: Exception) {
            alarmManager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, triggerMillis, triggerPendingIntent)
        }

        Log.i(TAG, "Snoozed alarm $alarmId for $minutes minutes (at $triggerMillis)")
        return triggerMillis
    }

    fun cancel(alarmId: String) {
        if (alarmManager == null) return

        val triggerIntent = Intent(context, AuraAlarmReceiver::class.java).apply {
            action = AuraAlarmReceiver.ACTION_TRIGGER
            putExtra(AuraAlarmReceiver.EXTRA_ALARM_ID, alarmId)
        }
        val triggerPendingIntent = PendingIntent.getBroadcast(
            context,
            alarmId.hashCode(),
            triggerIntent,
            PendingIntent.FLAG_NO_CREATE or PendingIntent.FLAG_IMMUTABLE
        )

        if (triggerPendingIntent != null) {
            alarmManager.cancel(triggerPendingIntent)
            triggerPendingIntent.cancel()
            Log.i(TAG, "Canceled alarm $alarmId in AlarmManager")
        }
    }

    fun rescheduleAll() {
        val alarms = store.getAll().filter { it.isEnabled }
        Log.i(TAG, "Rescheduling ${alarms.size} active alarms after boot/reset")
        alarms.forEach { schedule(it) }
    }

    companion object {
        private const val TAG = "AlarmScheduler"
    }
}
