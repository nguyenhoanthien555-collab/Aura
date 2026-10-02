package com.aura.companion.service

import com.aura.companion.MainActivity
import org.junit.Assert.assertEquals
import org.junit.Test

class AuraTileServiceTest {

    @Test
    fun `main activity constants are stable`() {
        assertEquals("start_voice", MainActivity.EXTRA_START_VOICE)
        assertEquals("initial_route", MainActivity.EXTRA_INITIAL_ROUTE)
        assertEquals("chat", MainActivity.ROUTE_CHAT)
    }

    @Test
    fun `expected intent flags cover single top and clear top`() {
        val expectedFlags = android.content.Intent.FLAG_ACTIVITY_NEW_TASK or
                android.content.Intent.FLAG_ACTIVITY_CLEAR_TOP or
                android.content.Intent.FLAG_ACTIVITY_SINGLE_TOP
        assertEquals(0x14000000 or 0x20000000, expectedFlags)
    }
}
