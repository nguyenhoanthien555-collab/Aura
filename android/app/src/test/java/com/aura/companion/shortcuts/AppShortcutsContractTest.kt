package com.aura.companion.shortcuts

import com.aura.companion.MainActivity
import com.aura.companion.ui.hub.HubRoutes
import org.junit.Assert.assertEquals
import org.junit.Test

class AppShortcutsContractTest {

    @Test
    fun `shortcut destinations map to known navigation routes`() {
        val shortcutDestinations = mapOf(
            "shortcut_chat" to MainActivity.ROUTE_CHAT,
            "shortcut_alarm" to HubRoutes.ALARMS,
            "shortcut_memory" to HubRoutes.MEMORY,
        )

        assertEquals("chat", shortcutDestinations["shortcut_chat"])
        assertEquals("hub/alarms", shortcutDestinations["shortcut_alarm"])
        assertEquals("hub/memory", shortcutDestinations["shortcut_memory"])
    }
}
