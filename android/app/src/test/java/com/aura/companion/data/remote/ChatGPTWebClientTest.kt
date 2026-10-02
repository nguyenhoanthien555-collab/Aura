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
        val difficulty = "000032"
        val token = ChatGPTWebClient.solveSentinelPow(seed, difficulty, maxIterations = 100_000)
        
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
}
