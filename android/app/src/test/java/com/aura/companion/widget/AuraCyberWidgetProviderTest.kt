package com.aura.companion.widget

import android.content.Context
import com.aura.companion.MainActivity
import com.aura.companion.alarm.data.AlarmStore
import com.aura.companion.alarm.data.IAlarmStore
import com.aura.companion.alarm.model.AuraAlarm
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AuraCyberWidgetProviderTest {

    private class FakeAlarmStore(private val alarmsList: List<AuraAlarm>) : IAlarmStore {
        override val alarms: StateFlow<List<AuraAlarm>> = MutableStateFlow(alarmsList)
        override fun getAll(): List<AuraAlarm> = alarmsList
        override fun get(id: String): AuraAlarm? = alarmsList.find { it.id == id }
        override fun save(alarm: AuraAlarm): AuraAlarm = alarm
        override fun delete(id: String): Boolean = true
        override fun toggle(id: String, enabled: Boolean): AuraAlarm? = null
        override fun calculateNextTriggerMillis(alarm: AuraAlarm, nowMillis: Long): Long {
            return (alarm.hour * 60 + alarm.minute).toLong()
        }
    }

    @Test
    fun `formatNextAlarm returns none when alarms list is empty`() {
        val fakeStore = FakeAlarmStore(emptyList())
        val result = AuraCyberWidgetProvider.formatNextAlarm(emptyList(), fakeStore)
        assertEquals("⏰ Không có", result)
    }

    @Test
    fun `formatNextAlarm returns none when all alarms are disabled`() {
        val alarms = listOf(
            AuraAlarm(id = "1", hour = 6, minute = 30, isEnabled = false),
            AuraAlarm(id = "2", hour = 8, minute = 0, isEnabled = false),
        )
        val fakeStore = FakeAlarmStore(alarms)
        val result = AuraCyberWidgetProvider.formatNextAlarm(alarms, fakeStore)
        assertEquals("⏰ Không có", result)
    }

    @Test
    fun `formatNextAlarm returns earliest enabled alarm formatted with label`() {
        val alarms = listOf(
            AuraAlarm(id = "1", hour = 9, minute = 0, label = "Họp team", isEnabled = true),
            AuraAlarm(id = "2", hour = 7, minute = 15, label = "Tập thể dục", isEnabled = true),
            AuraAlarm(id = "3", hour = 6, minute = 0, label = "Dậy sớm", isEnabled = false),
        )
        val fakeStore = FakeAlarmStore(alarms)
        val result = AuraCyberWidgetProvider.formatNextAlarm(alarms, fakeStore)
        assertEquals("⏰ 07:15 (Tập thể dục)", result)
    }

    @Test
    fun `formatNextAlarm omits default label`() {
        val alarms = listOf(
            AuraAlarm(id = "1", hour = 8, minute = 30, label = "Báo thức Aura", isEnabled = true),
        )
        val fakeStore = FakeAlarmStore(alarms)
        val result = AuraCyberWidgetProvider.formatNextAlarm(alarms, fakeStore)
        assertEquals("⏰ 08:30", result)
    }

    @Test
    fun `widget provider constants match activity contract`() {
        assertEquals("com.aura.companion.widget.ACTION_REFRESH", AuraCyberWidgetProvider.ACTION_REFRESH_WIDGET)
        assertEquals("start_voice", MainActivity.EXTRA_START_VOICE)
        assertEquals(1001, AuraCyberWidgetProvider.REQUEST_CODE_CHAT)
        assertEquals(1002, AuraCyberWidgetProvider.REQUEST_CODE_VOICE)
        assertEquals(1003, AuraCyberWidgetProvider.REQUEST_CODE_REFRESH)
    }
}
