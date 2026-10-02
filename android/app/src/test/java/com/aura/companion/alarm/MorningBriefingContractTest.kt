package com.aura.companion.alarm

import com.aura.companion.voice.AuraVoiceManager
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MorningBriefingContractTest {

    @Test
    fun `morning briefing text is clean for speech synthesis`() {
        val raw = "Chào buổi sáng anh! Aura chúc anh một ngày mới tràn đầy năng lượng và hiệu quả. Hệ thống đã thức dậy cùng anh và sẵn sàng đồng hành!"
        val cleaned = AuraVoiceManager.cleanForSpeech(raw)
        assertTrue(cleaned.isNotEmpty())
        assertFalse(cleaned.contains("#"))
        assertFalse(cleaned.contains("*"))
        assertFalse(cleaned.contains("`"))
    }

    @Test
    fun `vietnamese date formatting produces valid morning date string`() {
        val dateFormat = SimpleDateFormat("EEEE, dd 'tháng' MM", Locale("vi", "VN"))
        val formatted = dateFormat.format(Date(1727827200000L))
        assertTrue(formatted.isNotEmpty())
    }
}
