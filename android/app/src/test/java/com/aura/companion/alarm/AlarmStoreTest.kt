package com.aura.companion.alarm

import com.aura.companion.alarm.data.AlarmStore
import com.aura.companion.alarm.model.AuraAlarm
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar

class AlarmStoreTest {

    @Test
    fun `store performs in-memory save, toggle, and delete correctly`() {
        val store = AlarmStore(customPrefs = null)

        val alarm1 = AuraAlarm(
            id = "alarm_1",
            hour = 7,
            minute = 0,
            label = "Thức dậy",
            isEnabled = true,
            repeatDays = listOf(1, 2, 3, 4, 5)
        )
        val alarm2 = AuraAlarm(
            id = "alarm_2",
            hour = 8,
            minute = 30,
            label = "Tập gym",
            isEnabled = false,
            repeatDays = emptyList()
        )

        store.save(alarm1)
        store.save(alarm2)

        val all = store.getAll()
        assertEquals(2, all.size)
        assertEquals("alarm_1", all[0].id)
        assertEquals("alarm_2", all[1].id)

        // Toggle
        val toggled = store.toggle("alarm_2", true)
        assertNotNull(toggled)
        assertTrue(toggled!!.isEnabled)
        assertTrue(store.get("alarm_2")!!.isEnabled)

        // Delete
        val deleted = store.delete("alarm_1")
        assertTrue(deleted)
        assertEquals(1, store.getAll().size)
        assertNull(store.get("alarm_1"))
    }

    @Test
    fun `calculateNextTriggerMillis schedules today if time is in future`() {
        val store = AlarmStore(customPrefs = null)
        val nowCal = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, 6)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        val nowMillis = nowCal.timeInMillis

        val alarm = AuraAlarm(
            hour = 7,
            minute = 30,
            repeatDays = emptyList()
        )

        val triggerMillis = store.calculateNextTriggerMillis(alarm, nowMillis)
        val triggerCal = Calendar.getInstance().apply { timeInMillis = triggerMillis }

        assertEquals(nowCal.get(Calendar.DAY_OF_YEAR), triggerCal.get(Calendar.DAY_OF_YEAR))
        assertEquals(7, triggerCal.get(Calendar.HOUR_OF_DAY))
        assertEquals(30, triggerCal.get(Calendar.MINUTE))
    }

    @Test
    fun `calculateNextTriggerMillis schedules tomorrow for one-shot if time has passed`() {
        val store = AlarmStore(customPrefs = null)
        val nowCal = Calendar.getInstance().apply {
            set(Calendar.HOUR_OF_DAY, 10)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        val nowMillis = nowCal.timeInMillis

        val alarm = AuraAlarm(
            hour = 7,
            minute = 0,
            repeatDays = emptyList()
        )

        val triggerMillis = store.calculateNextTriggerMillis(alarm, nowMillis)
        val triggerCal = Calendar.getInstance().apply { timeInMillis = triggerMillis }

        assertEquals(nowCal.get(Calendar.DAY_OF_YEAR) + 1, triggerCal.get(Calendar.DAY_OF_YEAR))
        assertEquals(7, triggerCal.get(Calendar.HOUR_OF_DAY))
        assertEquals(0, triggerCal.get(Calendar.MINUTE))
    }
}
