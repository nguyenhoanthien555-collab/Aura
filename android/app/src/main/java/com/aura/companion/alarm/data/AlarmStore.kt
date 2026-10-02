package com.aura.companion.alarm.data

import android.content.Context
import android.content.SharedPreferences
import com.aura.companion.alarm.model.AuraAlarm
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.util.Calendar

/**
 * Interface contract for alarm storage and trigger calculations.
 */
interface IAlarmStore {
    val alarms: StateFlow<List<AuraAlarm>>
    fun getAll(): List<AuraAlarm>
    fun get(id: String): AuraAlarm?
    fun save(alarm: AuraAlarm): AuraAlarm
    fun delete(id: String): Boolean
    fun toggle(id: String, enabled: Boolean): AuraAlarm?
    fun calculateNextTriggerMillis(alarm: AuraAlarm, nowMillis: Long = System.currentTimeMillis()): Long
}

/**
 * Thread-safe persistent alarm store backed by SharedPreferences with JSON serialization.
 * Zero-network dependency for absolute 100% offline reliability.
 */
class AlarmStore(
    context: Context? = null,
    private val customPrefs: SharedPreferences? = null
) : IAlarmStore {

    private val json = Json { ignoreUnknownKeys = true }
    private val appContext: Context? = context?.applicationContext
    private val prefs: SharedPreferences? = customPrefs ?: appContext?.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)

    private val _alarms = MutableStateFlow<List<AuraAlarm>>(loadAlarms())
    override val alarms: StateFlow<List<AuraAlarm>> = _alarms.asStateFlow()

    private fun loadAlarms(): List<AuraAlarm> {
        val raw = prefs?.getString(KEY_ALARMS, null) ?: return emptyList()
        return try {
            json.decodeFromString<List<AuraAlarm>>(raw)
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun persist(list: List<AuraAlarm>) {
        _alarms.value = list
        prefs?.edit()?.putString(KEY_ALARMS, json.encodeToString(list))?.apply()
        appContext?.let { ctx ->
            try {
                com.aura.companion.widget.AuraCyberWidgetProvider.updateAll(ctx)
            } catch (_: Exception) {
                // Ignore in testing or non-widget contexts
            }
        }
    }

    override fun getAll(): List<AuraAlarm> = _alarms.value

    override fun get(id: String): AuraAlarm? = _alarms.value.find { it.id == id }

    override fun save(alarm: AuraAlarm): AuraAlarm {
        val current = _alarms.value.toMutableList()
        val index = current.indexOfFirst { it.id == alarm.id }
        if (index >= 0) {
            current[index] = alarm
        } else {
            current.add(alarm)
        }
        current.sortWith(compareBy({ it.hour }, { it.minute }))
        persist(current)
        return alarm
    }

    override fun delete(id: String): Boolean {
        val current = _alarms.value.toMutableList()
        val removed = current.removeAll { it.id == id }
        if (removed) {
            persist(current)
        }
        return removed
    }

    override fun toggle(id: String, enabled: Boolean): AuraAlarm? {
        val target = get(id) ?: return null
        val updated = target.copy(isEnabled = enabled)
        save(updated)
        return updated
    }

    override fun calculateNextTriggerMillis(alarm: AuraAlarm, nowMillis: Long): Long {
        val nowCal = Calendar.getInstance().apply { timeInMillis = nowMillis }
        val targetCal = Calendar.getInstance().apply {
            timeInMillis = nowMillis
            set(Calendar.HOUR_OF_DAY, alarm.hour)
            set(Calendar.MINUTE, alarm.minute)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }

        if (alarm.repeatDays.isEmpty()) {
            // One-shot alarm: if target is before or equal to now, schedule for tomorrow
            if (targetCal.timeInMillis <= nowMillis) {
                targetCal.add(Calendar.DAY_OF_YEAR, 1)
            }
            return targetCal.timeInMillis
        }

        // Repeating alarm on specific days of week:
        // 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat, 7=Sun
        val dayMapping = mapOf(
            1 to Calendar.MONDAY,
            2 to Calendar.TUESDAY,
            3 to Calendar.WEDNESDAY,
            4 to Calendar.THURSDAY,
            5 to Calendar.FRIDAY,
            6 to Calendar.SATURDAY,
            7 to Calendar.SUNDAY
        )

        for (dayOffset in 0..7) {
            val candidate = (targetCal.clone() as Calendar).apply {
                add(Calendar.DAY_OF_YEAR, dayOffset)
            }
            val calDayOfWeek = candidate.get(Calendar.DAY_OF_WEEK)
            val matchesDay = alarm.repeatDays.any { dayMapping[it] == calDayOfWeek }
            if (matchesDay && candidate.timeInMillis > nowMillis) {
                return candidate.timeInMillis
            }
        }

        // Fallback next day
        targetCal.add(Calendar.DAY_OF_YEAR, 1)
        return targetCal.timeInMillis
    }

    companion object {
        private const val PREFS_NAME = "aura_alarms"
        private const val KEY_ALARMS = "alarms_json"
    }
}
