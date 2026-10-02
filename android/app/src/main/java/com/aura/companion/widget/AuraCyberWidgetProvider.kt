package com.aura.companion.widget

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.widget.RemoteViews
import com.aura.companion.MainActivity
import com.aura.companion.R
import com.aura.companion.alarm.data.AlarmStore
import com.aura.companion.alarm.data.IAlarmStore
import com.aura.companion.alarm.model.AuraAlarm

/**
 * Android AppWidget provider for the Aura Cyber HUD home-screen widget.
 *
 * Displays:
 * 1. Aura Core status badge
 * 2. Handset battery % and charging status
 * 3. Next scheduled alarm
 * 4. Proactive companion insight / status
 * 5. 1-tap quick actions: Chat, Instant Voice Input, and Widget Refresh.
 */
class AuraCyberWidgetProvider : AppWidgetProvider() {

    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray
    ) {
        for (appWidgetId in appWidgetIds) {
            updateAppWidget(context, appWidgetManager, appWidgetId)
        }
    }

    override fun onReceive(context: Context, intent: Intent) {
        super.onReceive(context, intent)
        val action = intent.action ?: return
        if (action == ACTION_REFRESH_WIDGET ||
            action == Intent.ACTION_BATTERY_CHANGED ||
            action == Intent.ACTION_BOOT_COMPLETED ||
            action == "android.app.action.NEXT_ALARM_CLOCK_CHANGED"
        ) {
            updateAll(context)
        }
    }

    companion object {
        const val ACTION_REFRESH_WIDGET = "com.aura.companion.widget.ACTION_REFRESH"
        const val REQUEST_CODE_CHAT = 1001
        const val REQUEST_CODE_VOICE = 1002
        const val REQUEST_CODE_REFRESH = 1003

        fun sampleBattery(context: Context): Pair<Int, Boolean> {
            return try {
                val filter = IntentFilter(Intent.ACTION_BATTERY_CHANGED)
                val batteryStatus = context.registerReceiver(null, filter)
                val level = batteryStatus?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
                val scale = batteryStatus?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
                val status = batteryStatus?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
                val pct = if (level >= 0 && scale > 0) (level * 100 / scale) else -1
                val charging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
                    status == BatteryManager.BATTERY_STATUS_FULL
                Pair(pct, charging)
            } catch (_: Exception) {
                Pair(-1, false)
            }
        }

        fun formatNextAlarm(alarms: List<AuraAlarm>, alarmStore: IAlarmStore): String {
            val enabled = alarms.filter { it.isEnabled }
            if (enabled.isEmpty()) return "⏰ Không có"
            val next = enabled.minByOrNull { alarmStore.calculateNextTriggerMillis(it) } ?: return "⏰ Không có"
            return if (next.label.isNotBlank() && next.label != "Báo thức Aura") {
                "⏰ ${next.formattedTime} (${next.label})"
            } else {
                "⏰ ${next.formattedTime}"
            }
        }

        fun updateAppWidget(
            context: Context,
            appWidgetManager: AppWidgetManager,
            appWidgetId: Int
        ) {
            val views = RemoteViews(context.packageName, R.layout.widget_aura_cyber_hud)

            // 1. Handset Battery
            val (batteryPct, isCharging) = sampleBattery(context)
            val batteryText = if (batteryPct >= 0) {
                "📱 $batteryPct%${if (isCharging) " ⚡" else ""}"
            } else {
                "📱 100%"
            }
            views.setTextViewText(R.id.widget_battery, batteryText)

            // 2. Next Alarm
            val alarmStore = AlarmStore(context)
            val alarmText = formatNextAlarm(alarmStore.getAll(), alarmStore)
            views.setTextViewText(R.id.widget_alarm, alarmText)

            // 3. Proactive status
            views.setTextViewText(R.id.widget_insight, "◈ Sẵn sàng đồng hành cùng anh.")

            // 4. Chat action
            val chatIntent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val chatPending = PendingIntent.getActivity(
                context,
                REQUEST_CODE_CHAT,
                chatIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            views.setOnClickPendingIntent(R.id.widget_btn_chat, chatPending)
            views.setOnClickPendingIntent(R.id.widget_root, chatPending)

            // 5. Voice action
            val voiceIntent = Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
                putExtra(MainActivity.EXTRA_START_VOICE, true)
            }
            val voicePending = PendingIntent.getActivity(
                context,
                REQUEST_CODE_VOICE,
                voiceIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            views.setOnClickPendingIntent(R.id.widget_btn_voice, voicePending)

            // 6. Refresh action
            val refreshIntent = Intent(context, AuraCyberWidgetProvider::class.java).apply {
                action = ACTION_REFRESH_WIDGET
            }
            val refreshPending = PendingIntent.getBroadcast(
                context,
                REQUEST_CODE_REFRESH,
                refreshIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            views.setOnClickPendingIntent(R.id.widget_btn_refresh, refreshPending)

            appWidgetManager.updateAppWidget(appWidgetId, views)
        }

        fun updateAll(context: Context) {
            try {
                val appWidgetManager = AppWidgetManager.getInstance(context)
                val componentName = ComponentName(context, AuraCyberWidgetProvider::class.java)
                val ids = appWidgetManager.getAppWidgetIds(componentName)
                if (ids != null && ids.isNotEmpty()) {
                    for (id in ids) {
                        updateAppWidget(context, appWidgetManager, id)
                    }
                }
            } catch (_: Exception) {
                // Ignore in non-widget environments
            }
        }
    }
}
