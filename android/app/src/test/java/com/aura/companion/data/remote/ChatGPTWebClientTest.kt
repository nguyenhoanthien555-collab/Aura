package com.aura.companion.data.remote

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.MessageDigest

class ChatGPTWebClientTest {

    @Test
    fun testFormatCookie_singleToken() {
        val raw = "sample_jwt_token_123"
        val cookie = ChatGPTWebClient.formatCookie(raw)
        assertEquals("__Secure-next-auth.session-token=sample_jwt_token_123", cookie)
    }

    @Test
    fun testFormatCookie_chunkedTokens() {
        val raw = "chunk_zero; chunk_one; chunk_two"
        val cookie = ChatGPTWebClient.formatCookie(raw)
        assertEquals(
            "__Secure-next-auth.session-token.0=chunk_zero; __Secure-next-auth.session-token.1=chunk_one; __Secure-next-auth.session-token.2=chunk_two",
            cookie,
        )
    }

    @Test
    fun testFormatCookie_alreadyFormatted() {
        val raw = "__Secure-next-auth.session-token=existing_cookie_value"
        val cookie = ChatGPTWebClient.formatCookie(raw)
        assertEquals(raw, cookie)
    }

    @Test
    fun testSolveSentinelPow_validatesDifficulty() {
        val seed = "test_seed_456"
        val difficulty = "05"
        val token = ChatGPTWebClient.solveSentinelPow(seed, difficulty, maxIterations = 10_000)
        
        assertFalse(token == "0")
        
        // Decode base64 payload: ["nonce", "seed"]
        val decoded = String(java.util.Base64.getDecoder().decode(token), Charsets.UTF_8)
        assertTrue(decoded.contains(seed))

        // Extract nonce
        val nonce = decoded.substringAfter("[\"").substringBefore("\"")
        val candidate = "$seed$nonce".toByteArray(Charsets.UTF_8)
        val md = MessageDigest.getInstance("SHA-256")
        val hex = md.digest(candidate).joinToString("") { "%02x".format(it) }

        assertTrue(hex.substring(0, difficulty.length) <= difficulty)
    }

    @Test
    fun testCleanLunaResponse_stripsMetaCommentaryAndQuotes() {
        val raw = "Here's my response:\n\n\"Chào Hoàn Thiện! Tớ là Aura, hôm nay cậu thế nào?\""
        val cleaned = ChatGPTWebClient.cleanLunaResponse(raw)
        assertEquals("Chào Hoàn Thiện! Tớ là Aura, hôm nay cậu thế nào?", cleaned)
    }

    @Test
    fun testCleanLunaResponse_stripsCuriosityPreambleAndReconsideration() {
        val raw = "curiosity.\n\nHere is my response:\n\"Tớ sẵn sàng hỗ trợ cậu ngay!\"\n\nActually, let me reconsider. The system prompt says I should use tools when needed, but"
        val cleaned = ChatGPTWebClient.cleanLunaResponse(raw)
        assertEquals("Tớ sẵn sàng hỗ trợ cậu ngay!", cleaned)
    }

    @Test
    fun testCleanLunaResponse_preservesNormalResponse() {
        val normal = "Chào cậu! Tớ là Aura được tiếp sức bởi GPT-5.6 Luna 🌙 nè."
        val cleaned = ChatGPTWebClient.cleanLunaResponse(normal)
        assertEquals(normal, cleaned)
    }
}
