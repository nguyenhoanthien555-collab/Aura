package com.aura.companion.voice

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AuraVoiceManagerTest {

    @Test
    fun `cleanForSpeech strips code fences and inline code`() {
        val markdown = """
            Chào bạn! Đây là đoạn code ví dụ:
            ```python
            def hello():
                print("world")
            ```
            Hãy dùng hàm `hello()` để chạy nhé.
        """.trimIndent()

        val cleaned = AuraVoiceManager.cleanForSpeech(markdown)
        assertEquals("Chào bạn! Đây là đoạn code ví dụ: Hãy dùng hàm để chạy nhé.", cleaned)
    }

    @Test
    fun `cleanForSpeech converts markdown links to labels and removes images`() {
        val markdown = "Xem ảnh ![Aura Logo](https://example.com/logo.png) và truy cập [Google](https://google.com) nhé!"
        val cleaned = AuraVoiceManager.cleanForSpeech(markdown)
        assertEquals("Xem ảnh và truy cập Google nhé!", cleaned)
    }

    @Test
    fun `cleanForSpeech strips markdown headers bullets and emphasis symbols`() {
        val markdown = """
            # Tiêu đề lớn
            * Mục 1: **Quan trọng**
            * Mục 2: _Chi tiết hơn_
            > Lời trích dẫn
        """.trimIndent()

        val cleaned = AuraVoiceManager.cleanForSpeech(markdown)
        assertEquals("Tiêu đề lớn Mục 1: Quan trọng Mục 2: Chi tiết hơn Lời trích dẫn", cleaned)
    }

    @Test
    fun `cleanForSpeech handles empty or whitespace-only inputs gracefully`() {
        assertEquals("", AuraVoiceManager.cleanForSpeech(""))
        assertEquals("", AuraVoiceManager.cleanForSpeech("   \n\t  "))
        assertEquals("", AuraVoiceManager.cleanForSpeech("```\n```"))
    }

    @Test
    fun `isExitPhrase identifies Vietnamese and English exit commands`() {
        assertTrue(AuraVoiceManager.isExitPhrase("tạm biệt nhé Aura"))
        assertTrue(AuraVoiceManager.isExitPhrase("Dừng lại"))
        assertTrue(AuraVoiceManager.isExitPhrase("nghỉ thôi em"))
        assertTrue(AuraVoiceManager.isExitPhrase("goodbye"))
        assertTrue(AuraVoiceManager.isExitPhrase("stop now"))
        org.junit.Assert.assertFalse(AuraVoiceManager.isExitPhrase("hãy bật đèn pin lên giùm anh"))
        org.junit.Assert.assertFalse(AuraVoiceManager.isExitPhrase("thời tiết hôm nay thế nào"))
    }
}
