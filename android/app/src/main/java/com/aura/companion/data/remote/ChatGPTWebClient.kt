package com.aura.companion.data.remote

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.BufferedReader
import java.io.InputStreamReader
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Direct client for ChatGPT Web (chatgpt.com) using residential mobile IP.
 *
 * Runs natively on Android (OPPO Reno6 5G) on 4G/Wi-Fi to overcome Cloudflare WAF
 * datacenter IP restrictions (HTTP 403 Forbidden).
 */
object ChatGPTWebClient {

    private const val DEFAULT_USER_AGENT =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

    private const val BASE_URL = "https://chatgpt.com"

    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }

    data class SessionResult(
        val ok: Boolean,
        val email: String? = null,
        val accessToken: String? = null,
        val error: String? = null,
        val httpCode: Int = 0,
    )

    fun formatCookie(rawToken: String): String {
        val trimmed = rawToken.trim()
        if (trimmed.isEmpty()) return ""
        if (trimmed.contains("__Secure-next-auth.session-token") && trimmed.contains("=")) {
            return trimmed
        }
        val parts = if (trimmed.contains(";")) {
            trimmed.split(";").map { it.trim() }.filter { it.isNotEmpty() }
        } else if (trimmed.contains("\n")) {
            trimmed.split("\n").map { it.trim() }.filter { it.isNotEmpty() }
        } else {
            listOf(trimmed)
        }

        if (parts.size >= 2) {
            return parts.mapIndexed { idx, p -> "__Secure-next-auth.session-token.$idx=$p" }.joinToString("; ")
        }
        return "__Secure-next-auth.session-token=${parts[0]}"
    }

    private fun base64Encode(bytes: ByteArray): String {
        return try {
            java.util.Base64.getEncoder().encodeToString(bytes)
        } catch (_: Throwable) {
            android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP)
        }
    }

    fun solveSentinelPow(seed: String, difficulty: String, maxIterations: Int = 500_000): String {
        val diffLen = difficulty.length
        val md = MessageDigest.getInstance("SHA-256")
        for (nonce in 0 until maxIterations) {
            val candidate = "$seed$nonce".toByteArray(Charsets.UTF_8)
            val digest = md.digest(candidate)
            val sb = StringBuilder()
            for (b in digest) {
                sb.append("%02x".format(b))
            }
            val hex = sb.toString()
            if (hex.length >= diffLen && hex.substring(0, diffLen) <= difficulty) {
                val payload = "[\"$nonce\", \"$seed\"]"
                return base64Encode(payload.toByteArray(Charsets.UTF_8))
            }
        }
        return "0"
    }

    suspend fun verifySession(sessionToken: String): SessionResult = withContext(Dispatchers.IO) {
        if (sessionToken.isBlank()) {
            return@withContext SessionResult(ok = false, error = "Session token trống")
        }
        val cookie = formatCookie(sessionToken)
        val request = Request.Builder()
            .url("$BASE_URL/api/auth/session")
            .header("User-Agent", DEFAULT_USER_AGENT)
            .header("Accept", "application/json")
            .header("Cookie", cookie)
            .build()

        try {
            httpClient.newCall(request).execute().use { response ->
                val code = response.code
                val bodyStr = response.body?.string().orEmpty()
                if (code == 200) {
                    val root = json.parseToJsonElement(bodyStr).jsonObject
                    val accessToken = root["accessToken"]?.jsonPrimitive?.content
                    val userObj = root["user"]?.jsonObject
                    val email = userObj?.get("email")?.jsonPrimitive?.content
                    if (!accessToken.isNullOrBlank()) {
                        SessionResult(
                            ok = true,
                            email = email ?: "Tài khoản Clone",
                            accessToken = accessToken,
                            httpCode = code,
                        )
                    } else {
                        SessionResult(
                            ok = false,
                            error = "Không tìm thấy accessToken trong session response",
                            httpCode = code,
                        )
                    }
                } else if (code == 401) {
                    SessionResult(ok = false, error = "HTTP 401 - Session Token không hợp lệ hoặc đã hết hạn", httpCode = code)
                } else if (code == 403) {
                    SessionResult(ok = false, error = "HTTP 403 - Cloudflare chặn (hãy tắt VPN)", httpCode = code)
                } else {
                    SessionResult(ok = false, error = "HTTP $code: $bodyStr", httpCode = code)
                }
            }
        } catch (e: Exception) {
            SessionResult(ok = false, error = "Lỗi kết nối: ${e.localizedMessage ?: e.javaClass.simpleName}")
        }
    }

    suspend fun streamConversation(
        prompt: String,
        sessionToken: String,
        model: String = "auto",
        onChunk: (String) -> Unit,
    ): Result<String> = withContext(Dispatchers.IO) {
        val session = verifySession(sessionToken)
        if (!session.ok || session.accessToken.isNullOrBlank()) {
            return@withContext Result.failure(IllegalStateException(session.error ?: "Session không hợp lệ"))
        }

        val deviceId = UUID.randomUUID().toString()

        // 1. Fetch Sentinel Chat Requirements & Proof of Work
        var reqToken: String? = null
        var proofToken: String? = null
        try {
            val reqPayload = "{\"p\":\"\"}".toRequestBody("application/json".toMediaType())
            val reqRequest = Request.Builder()
                .url("$BASE_URL/backend-api/sentinel/chat-requirements")
                .header("User-Agent", DEFAULT_USER_AGENT)
                .header("Authorization", "Bearer ${session.accessToken}")
                .header("Content-Type", "application/json")
                .header("oai-device-id", deviceId)
                .post(reqPayload)
                .build()

            httpClient.newCall(reqRequest).execute().use { resp ->
                if (resp.isSuccessful) {
                    val bodyStr = resp.body?.string().orEmpty()
                    val elem = json.parseToJsonElement(bodyStr).jsonObject
                    reqToken = elem["token"]?.jsonPrimitive?.content
                    val pow = elem["proofofwork"]?.jsonObject
                    if (pow != null && pow["required"]?.jsonPrimitive?.content == "true") {
                        val seed = pow["seed"]?.jsonPrimitive?.content.orEmpty()
                        val diff = pow["difficulty"]?.jsonPrimitive?.content ?: "000032"
                        if (seed.isNotEmpty()) {
                            proofToken = solveSentinelPow(seed, diff)
                        }
                    }
                }
            }
        } catch (_: Exception) {
            // Non-fatal, proceed to conversation
        }

        // 2. Stream /backend-api/conversation
        val messageId = UUID.randomUUID().toString()
        val parentId = UUID.randomUUID().toString()
        val escapedPrompt = JsonPrimitive(prompt).toString()
        val convPayload = """
        {
            "action": "next",
            "messages": [
                {
                    "id": "$messageId",
                    "author": {"role": "user"},
                    "content": {
                        "content_type": "text",
                        "parts": [$escapedPrompt]
                    },
                    "metadata": {}
                }
            ],
            "parent_message_id": "$parentId",
            "model": "$model",
            "timezone_offset_min": -420,
            "suggestions": [],
            "history_and_training_disabled": false,
            "conversation_mode": {"kind": "primary_assistant"},
            "force_paragen": false
        }
        """.trimIndent().toRequestBody("application/json".toMediaType())

        val reqBuilder = Request.Builder()
            .url("$BASE_URL/backend-api/conversation")
            .header("User-Agent", DEFAULT_USER_AGENT)
            .header("Authorization", "Bearer ${session.accessToken}")
            .header("Content-Type", "application/json")
            .header("Accept", "text/event-stream")
            .header("oai-device-id", deviceId)
            .post(convPayload)

        if (!reqToken.isNullOrBlank()) {
            reqBuilder.header("openai-sentinel-chat-requirements-token", reqToken!!)
        }
        if (!proofToken.isNullOrBlank()) {
            reqBuilder.header("openai-sentinel-proof-token", proofToken!!)
        }

        val fullTextBuilder = StringBuilder()
        var lastLength = 0

        try {
            httpClient.newCall(reqBuilder.build()).execute().use { response ->
                if (!response.isSuccessful) {
                    val err = response.body?.string().orEmpty()
                    return@withContext Result.failure(IllegalStateException("HTTP ${response.code}: $err"))
                }
                val stream = response.body?.byteStream()
                    ?: return@withContext Result.failure(IllegalStateException("Empty response body"))
                val reader = BufferedReader(InputStreamReader(stream, Charsets.UTF_8))
                var line: String? = reader.readLine()
                while (line != null) {
                    val trimmed = line.trim()
                    if (trimmed.startsWith("data: ")) {
                        val dataStr = trimmed.removePrefix("data: ").trim()
                        if (dataStr == "[DONE]") {
                            break
                        }
                        try {
                            val dataObj = json.parseToJsonElement(dataStr).jsonObject
                            val msg = dataObj["message"]?.jsonObject
                            val content = msg?.get("content")?.jsonObject
                            val parts = content?.get("parts")?.jsonArray
                            if (parts != null && parts.isNotEmpty()) {
                                val fullPart = parts[0].jsonPrimitive.content
                                if (fullPart.length > lastLength) {
                                    val delta = fullPart.substring(lastLength)
                                    lastLength = fullPart.length
                                    fullTextBuilder.append(delta)
                                    onChunk(delta)
                                }
                            }
                        } catch (_: Exception) {
                            // ignore unparseable metadata events
                        }
                    }
                    line = reader.readLine()
                }
            }
            Result.success(fullTextBuilder.toString())
        } catch (e: Exception) {
            Result.failure(e)
        }
    }
}
