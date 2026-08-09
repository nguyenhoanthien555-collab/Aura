package com.aura.companion.data

import com.aura.companion.data.settings.FakeSettings
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * §21: "chat request", "error handling", "authentication",
 * "notification handling".
 *
 * Run against a real HTTP server on loopback rather than a mocked
 * Retrofit interface. The bugs this layer actually has are in the wire
 * format - a field named `sessionId` where the server wants `session_id`
 * type-checks perfectly and fails at runtime - and only a real request
 * catches those.
 */
class AuraRepositoryTest {

    private lateinit var server: MockWebServer
    private lateinit var settings: FakeSettings
    private lateinit var repository: AuraRepository

    private val json = Json { ignoreUnknownKeys = true }

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()

        settings = FakeSettings(
            serverUrl = server.url("/").toString(),
            authToken = "test-token",
        )

        repository = AuraRepository(settings)
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    // ------------------------------------------------------------------
    // Chat request
    // ------------------------------------------------------------------

    @Test
    fun `chat posts to api slash chat in the server's field names`() = runTest {

        server.enqueue(chatResponse(sessionId = "s-1", reply = "Hello."))

        val result = repository.send("Are you there?")

        val request = server.takeRequest()

        assertEquals("/api/chat", request.path)
        assertEquals("POST", request.method)

        val body = json.parseToJsonElement(request.body.readUtf8()) as JsonObject

        // Snake case, because that is what server/models.py declares.
        assertEquals("Are you there?", body["message"]?.jsonPrimitive?.content)
        assertTrue("session_id must be present, even as null", body.containsKey("session_id"))

        assertTrue(result is AuraResult.Ok)
        assertEquals("Hello.", (result as AuraResult.Ok).value.reply)
    }

    @Test
    fun `the first message carries no session and the second carries the server's`() = runTest {

        server.enqueue(chatResponse(sessionId = "generated-by-server", reply = "One."))
        server.enqueue(chatResponse(sessionId = "generated-by-server", reply = "Two."))

        repository.send("first")
        repository.send("second")

        val first = json.parseToJsonElement(server.takeRequest().body.readUtf8()) as JsonObject
        val second = json.parseToJsonElement(server.takeRequest().body.readUtf8()) as JsonObject

        // Null, not "" - the server generates a session when it sees none,
        // and an empty string is a session id it would try to look up.
        assertNull(first["session_id"]?.jsonPrimitive?.contentOrNull)

        // This is the whole of conversational continuity: whatever the
        // server named itself, the app says back.
        assertEquals(
            "generated-by-server",
            second["session_id"]?.jsonPrimitive?.content,
        )
        assertEquals("generated-by-server", repository.sessionId)
    }

    @Test
    fun `resetSession makes the next message start a new conversation`() = runTest {

        server.enqueue(chatResponse(sessionId = "old", reply = "One."))
        repository.send("first")
        server.takeRequest()

        repository.resetSession()
        assertNull(repository.sessionId)

        server.enqueue(chatResponse(sessionId = "new", reply = "Two."))
        repository.send("second")

        val body = json.parseToJsonElement(server.takeRequest().body.readUtf8()) as JsonObject
        assertNull(body["session_id"]?.jsonPrimitive?.contentOrNull)
    }

    @Test
    fun `the client identifies itself in metadata`() = runTest {

        server.enqueue(chatResponse(sessionId = "s-1", reply = "Hi."))

        repository.send("hello")

        val body = json.parseToJsonElement(server.takeRequest().body.readUtf8()) as JsonObject
        val metadata = body["metadata"] as? JsonObject

        assertEquals("android", metadata?.get("client")?.jsonPrimitive?.content)
    }

    // ------------------------------------------------------------------
    // Authentication
    // ------------------------------------------------------------------

    @Test
    fun `every request carries the bearer token`() = runTest {

        server.enqueue(chatResponse(sessionId = "s-1", reply = "Hi."))

        repository.send("hello")

        assertEquals("Bearer test-token", server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `a token changed in settings takes effect on the next request`() = runTest {

        server.enqueue(chatResponse(sessionId = "s-1", reply = "One."))
        repository.send("first")
        server.takeRequest()

        settings.current = settings.current.copy(authToken = "rotated-token")

        server.enqueue(chatResponse(sessionId = "s-1", reply = "Two."))
        repository.send("second")

        // Read per request, not captured at construction - otherwise a
        // corrected token only works after an app restart.
        assertEquals("Bearer rotated-token", server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `no Authorization header is sent when no token is configured`() = runTest {

        settings.current = settings.current.copy(authToken = "")

        server.enqueue(chatResponse(sessionId = "s-1", reply = "Hi."))
        repository.send("hello")

        // An empty bearer is worse than none: it reads as a malformed
        // credential rather than an absent one.
        assertNull(server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `an unconfigured server URL fails before any request`() = runTest {

        val offline = AuraRepository(FakeSettings(serverUrl = ""))

        val result = offline.send("hello")

        assertEquals(AuraError.NotConfigured, (result as AuraResult.Failed).error)
        assertEquals(0, server.requestCount)
    }

    // ------------------------------------------------------------------
    // Error handling
    // ------------------------------------------------------------------

    @Test
    fun `401 and 403 both mean the token was refused`() = runTest {

        server.enqueue(MockResponse().setResponseCode(401).setBody("""{"detail":"bad token"}"""))
        assertEquals(AuraError.Unauthorized, failureOf(repository.send("hi")))

        server.enqueue(MockResponse().setResponseCode(403).setBody("""{"detail":"forbidden"}"""))
        assertEquals(AuraError.Unauthorized, failureOf(repository.send("hi")))
    }

    @Test
    fun `502 and 504 are read as a cold start rather than a fault`() = runTest {

        // A free-tier edge answering while the container boots. Telling the
        // user to check their settings here is how a correct configuration
        // gets changed for no reason.
        server.enqueue(MockResponse().setResponseCode(502).setBody("Bad Gateway"))
        assertEquals(AuraError.Waking, failureOf(repository.send("hi")))

        server.enqueue(MockResponse().setResponseCode(504).setBody("Gateway Timeout"))
        assertEquals(AuraError.Waking, failureOf(repository.send("hi")))
    }

    @Test
    fun `a bare 503 is a cold start`() = runTest {

        // The platform's own "no healthy upstream", with no Aura behind it.
        server.enqueue(MockResponse().setResponseCode(503).setBody("Service Unavailable"))

        assertEquals(AuraError.Waking, failureOf(repository.send("hi")))
    }

    @Test
    fun `a 503 naming screen_disabled is Aura declining, not Aura waking`() = runTest {

        // The one 503 Aura itself sends - server/routes/screen.py, when
        // observation is switched off server-side. It leads the user
        // somewhere different from a cold start, so it must not be
        // collapsed into one.
        server.enqueue(
            MockResponse()
                .setResponseCode(503)
                .setBody("""{"error":"screen_disabled"}""")
        )

        val error = failureOf(
            repository.sendScreen(
                application = "Reader",
                packageName = "com.example.reader",
                screenText = "a page of text",
            )
        )

        assertTrue("expected Unavailable, got $error", error is AuraError.Unavailable)
        assertTrue(error.userMessage.contains("switched off"))
    }

    @Test
    fun `a 500 is reported with its code and never its body`() = runTest {

        // The body can name an internal path. It is matched against for
        // screen_disabled and otherwise dropped.
        server.enqueue(
            MockResponse()
                .setResponseCode(500)
                .setBody("""{"error":"chat_failed","trace":"/srv/aura/brain/router.py"}""")
        )

        val error = failureOf(repository.send("hi"))

        assertEquals(AuraError.ServerFailure(500), error)
        assertFalse(error.userMessage.contains("/srv"))
        assertFalse(error.userMessage.contains("router.py"))
    }

    @Test
    fun `a malformed body is a failure, not a crash`() = runTest {

        server.enqueue(MockResponse().setResponseCode(200).setBody("this is not json"))

        val result = repository.send("hi")

        assertTrue(result is AuraResult.Failed)
    }

    @Test
    fun `a dropped connection becomes Offline`() = runTest {

        server.enqueue(
            MockResponse().setSocketPolicy(
                okhttp3.mockwebserver.SocketPolicy.DISCONNECT_AT_START
            )
        )

        assertEquals(AuraError.Offline, failureOf(repository.send("hi")))
    }

    // ------------------------------------------------------------------
    // Screen
    // ------------------------------------------------------------------

    @Test
    fun `a screen observation carries the device id and the session`() = runTest {

        server.enqueue(chatResponse(sessionId = "s-42", reply = "Hi."))
        repository.send("hello")
        server.takeRequest()

        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"session_id":"s-42","status":"accepted","accepted":true,
                   "decision":{"should_notify":true,"message":"Want a hand?",
                   "reason":"stuck","priority":"normal","confidence":0.8}}"""
            )
        )

        val result = repository.sendScreen(
            application = "Editor",
            packageName = "com.example.editor",
            screenText = "a stack trace",
        )

        val request = server.takeRequest()
        assertEquals("/api/screen", request.path)

        val body = json.parseToJsonElement(request.body.readUtf8()) as JsonObject
        assertEquals("s-42", body["session_id"]?.jsonPrimitive?.content)
        assertEquals("android-test", body["device_id"]?.jsonPrimitive?.content)
        assertEquals("com.example.editor", body["package"]?.jsonPrimitive?.content)
        assertEquals("a stack trace", body["screen_text"]?.jsonPrimitive?.content)

        val decision = (result as AuraResult.Ok).value
        assertEquals(true, decision?.shouldNotify)
        assertEquals("Want a hand?", decision?.message)
    }

    @Test
    fun `a screen the server ignores yields no decision`() = runTest {

        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"session_id":"s-1","status":"ignored","accepted":false}"""
            )
        )

        val result = repository.sendScreen("App", "com.example.app", "text")

        // Not an error. "Nothing worth saying" is the companion working.
        assertNull((result as AuraResult.Ok).value)
    }

    // ------------------------------------------------------------------
    // Notifications
    // ------------------------------------------------------------------

    @Test
    fun `notifications are fetched for this device and parsed`() = runTest {

        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"notifications":[
                     {"notification_id":"n-1","message":"You have been at this a while.",
                      "reason":"long_session","priority":"low","confidence":0.7,
                      "source":"companion","created_at":1700000000.0}],
                   "count":1,"companion_enabled":true}"""
            )
        )

        val result = repository.collectNotifications()

        assertEquals("/api/notifications?device_id=android-test", server.takeRequest().path)

        val notifications = (result as AuraResult.Ok).value
        assertEquals(1, notifications.size)
        assertEquals("n-1", notifications.first().notificationId)
        assertEquals("You have been at this a while.", notifications.first().message)
    }

    @Test
    fun `an empty notification list is a success with nothing in it`() = runTest {

        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"notifications":[],"count":0,"companion_enabled":true}"""
            )
        )

        assertTrue((repository.collectNotifications() as AuraResult.Ok).value.isEmpty())
    }

    // ------------------------------------------------------------------
    // Health
    // ------------------------------------------------------------------

    @Test
    fun `health exposes the runtime map the settings screen reads`() = runTest {

        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"status":"ok","version":"0.1.0","uptime_seconds":12.5,
                   "runtime":{"llm_provider":"ollama","memory":"sqlite"}}"""
            )
        )

        val health = (repository.health() as AuraResult.Ok).value

        assertEquals("/api/health", server.takeRequest().path)
        assertEquals("ok", health.status)
        assertEquals("0.1.0", health.version)
        assertEquals("ollama", health.runtime["llm_provider"])
    }

    // ------------------------------------------------------------------

    private fun chatResponse(sessionId: String, reply: String) =
        MockResponse().setResponseCode(200).setBody(
            """{"session_id":"$sessionId","reply":"$reply","message_id":"m-1"}"""
        )

    private fun failureOf(result: AuraResult<*>): AuraError =
        (result as AuraResult.Failed).error
}
