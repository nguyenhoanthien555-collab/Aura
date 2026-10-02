package com.aura.companion.work

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

class DirectReplyContractTest {

    @Test
    fun `direct reply constants match contract`() {
        assertEquals("com.aura.companion.ACTION_DIRECT_REPLY", DirectReplyReceiver.ACTION_DIRECT_REPLY)
        assertEquals("key_aura_direct_reply", DirectReplyReceiver.KEY_TEXT_REPLY)
        assertEquals("extra_notification_id", DirectReplyReceiver.EXTRA_NOTIFICATION_ID)
    }

    @Test
    fun `direct reply receiver class exists and can be instantiated`() {
        val receiver = DirectReplyReceiver()
        assertNotNull(receiver)
    }
}
