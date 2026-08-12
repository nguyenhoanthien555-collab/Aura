package com.aura.companion.data.remote

import com.aura.companion.data.AuraError
import com.aura.companion.data.settings.FakeSettings
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * §21: streamed chat.
 *
 * The protocol lives in `server/routes/ws_chat.py`, and the two ends can
 * drift apart without either failing to compile. These drive a real
 * WebSocket on loopback and assert on the frames actually exchanged.
 *
 * `runBlocking`, not `runTest`, on purpose. `runTest` runs on a virtual
 * clock that skips delays, so a `withTimeout` around real socket I/O would
 * fire the moment the collector suspended - every test would "time out" in
 * microseconds while the socket was working perfectly.
 */
class AuraStreamClientTest {

    private lateinit var server: MockWebServer

    /** The frame the client sent after the socket opened. */
    private val clientFrame = java.util.concurrent.atomic.AtomicReference<String?>(null)

    private val frameReceived = CountDownLatch(1)

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    /**
     * A server that records the client's frame, then replies with [script].
     */
    private fun clientFor(
        token: String = "test-token",
        script: (WebSocket) -> Unit,
    ): AuraStreamClient {

        server.enqueue(
            MockResponse().withWebSocketUpgrade(
                object : WebSocketListener() {
                    override fun onMessage(webSocket: WebSocket, text: String) {
                        clientFrame.set(text)
                        frameReceived.countDown()
                        script(webSocket)
                        // The production endpoint closes in its `finally`
                        // block after sending its terminal frame.
                        webSocket.close(1000, null)
                    }
                }
            )
        )

        return AuraStreamClient(
            FakeSettings(
                serverUrl = server.url("/").toString(),
                authToken = token,
            )
        )
    }

    private fun collect(
        client: AuraStreamClient,
        message: String = "hello",
        sessionId: String? = null,
    ): List<StreamEvent> = runBlocking {
        withTimeout(SOCKET_TIMEOUT) {
            client.stream(message, sessionId).toList()
        }
    }

    // ------------------------------------------------------------------
    // The happy path
    // ------------------------------------------------------------------

    @Test
    fun `a full reply arrives as started, chunks, complete`() {

        val client = clientFor { socket ->
            socket.send("""{"type":"started","session_id":"s-1","message_id":"m-1"}""")
            socket.send("""{"type":"chunk","chunk":"Hello","index":0}""")
            socket.send("""{"type":"chunk","chunk":", there.","index":1}""")
            socket.send(
                """{"type":"complete","session_id":"s-1","message_id":"m-1",
                   "total_chunks":2,"elapsed_seconds":1.5,"first_chunk_seconds":0.4}"""
            )
        }

        val events = collect(client)

        assertEquals(4, events.size)

        assertEquals("s-1", (events[0] as StreamEvent.Started).sessionId)
        assertEquals("Hello", (events[1] as StreamEvent.Chunk).text)
        assertEquals(", there.", (events[2] as StreamEvent.Chunk).text)

        val complete = events[3] as StreamEvent.Complete
        assertEquals("s-1", complete.sessionId)
        assertEquals(2, complete.totalChunks)
        assertEquals(1.5, complete.elapsedSeconds, 0.001)
        assertEquals(0.4, complete.firstChunkSeconds!!, 0.001)
    }

    @Test
    fun `chunks concatenate into the reply in order`() {

        val client = clientFor { socket ->
            socket.send("""{"type":"chunk","chunk":"The ","index":0}""")
            socket.send("""{"type":"chunk","chunk":"lighthouse ","index":1}""")
            socket.send("""{"type":"chunk","chunk":"keeper.","index":2}""")
            socket.send("""{"type":"complete","session_id":"s-1","message_id":"m-1"}""")
        }

        val text = collect(client)
            .filterIsInstance<StreamEvent.Chunk>()
            .joinToString("") { it.text }

        assertEquals("The lighthouse keeper.", text)
    }

    @Test
    fun `a complete frame without timings is still valid`() {

        // `first_chunk_seconds` is absent when nothing streamed.
        val client = clientFor { socket ->
            socket.send("""{"type":"complete","session_id":"s-1","message_id":"m-1"}""")
        }

        val complete = collect(client).last() as StreamEvent.Complete

        assertEquals("s-1", complete.sessionId)
        assertNull(complete.firstChunkSeconds)
    }

    // ------------------------------------------------------------------
    // What goes out
    // ------------------------------------------------------------------

    @Test
    fun `the message is sent as one frame carrying no session`() {

        val client = clientFor { socket ->
            socket.send("""{"type":"complete","session_id":"s-1","message_id":"m-1"}""")
        }

        collect(client, message = "what is the weather")

        assertTrue(frameReceived.await(2, TimeUnit.SECONDS))

        val sent = clientFrame.get().orEmpty()

        assertTrue("frame was: $sent", sent.contains("\"message\""))
        assertTrue("frame was: $sent", sent.contains("what is the weather"))

        // The session travels in the query string at handshake time. In the
        // body as well, it would be two sources of truth for one fact.
        assertFalse("frame must not carry a session: $sent", sent.contains("session_id"))
    }

    @Test
    fun `the token and session travel in the handshake query string`() {

        val client = clientFor(token = "secret-token") { socket ->
            socket.send("""{"type":"complete","session_id":"s-9","message_id":"m-1"}""")
        }

        collect(client, message = "hi", sessionId = "s-9")

        val path = server.takeRequest().path.orEmpty()

        // A WebSocket handshake has nowhere to put an Authorization
        // header, which is why the server reads `?token=`.
        assertTrue("path was: $path", path.startsWith("/api/chat/stream"))
        assertTrue("path was: $path", path.contains("token=secret-token"))
        assertTrue("path was: $path", path.contains("session_id=s-9"))
    }

    @Test
    fun `no session parameter is sent on the first message`() {

        val client = clientFor { socket ->
            socket.send("""{"type":"complete","session_id":"s-1","message_id":"m-1"}""")
        }

        collect(client, sessionId = null)

        val path = server.takeRequest().path.orEmpty()

        // The server generates one when it sees none. An empty string would
        // be a session id it tries to look up.
        assertFalse("path was: $path", path.contains("session_id"))
    }

    // ------------------------------------------------------------------
    // Failures
    // ------------------------------------------------------------------

    @Test
    fun `a server error frame becomes a typed failure`() {

        val client = clientFor { socket ->
            socket.send("""{"type":"error","error":"message_too_long"}""")
        }

        // Mapped, not shown. `message_too_long` is protocol vocabulary.
        assertEquals(
            AuraError.ServerFailure(413),
            (collect(client).last() as StreamEvent.Failed).error,
        )
    }

    @Test
    fun `an empty message rejection is reported as unprocessable`() {

        val client = clientFor { socket ->
            socket.send("""{"type":"error","error":"empty_message"}""")
        }

        assertEquals(
            AuraError.ServerFailure(422),
            (collect(client).last() as StreamEvent.Failed).error,
        )
    }

    @Test
    fun `a refused handshake is reported as a rejected token`() {

        server.enqueue(MockResponse().setResponseCode(403).setBody("forbidden"))

        val client = AuraStreamClient(
            FakeSettings(serverUrl = server.url("/").toString(), authToken = "wrong")
        )

        // The upgrade never happened, so this arrives as an ordinary HTTP
        // status - which is how a bad `?token=` is told from a dead network.
        // 403 rather than 401: the socket path uses the same vocabulary as the
        // REST path, where the two say different things about the token.
        assertEquals(
            AuraError.Forbidden,
            (collect(client).last() as StreamEvent.Failed).error,
        )
    }

    @Test
    fun `a gateway error during a cold start is not reported as a fault`() {

        server.enqueue(MockResponse().setResponseCode(502).setBody("Bad Gateway"))

        val client = AuraStreamClient(
            FakeSettings(serverUrl = server.url("/").toString(), authToken = "t")
        )

        assertEquals(
            AuraError.Waking,
            (collect(client).last() as StreamEvent.Failed).error,
        )
    }

    @Test
    fun `a socket closed mid-reply terminates without a terminal frame`() {

        val client = clientFor { socket ->
            socket.send("""{"type":"started","session_id":"s-1","message_id":"m-1"}""")
            socket.send("""{"type":"chunk","chunk":"Half a sen","index":0}""")
            // Closes without `complete` - a proxy timing out mid-reply.
            socket.close(1000, null)
        }

        val events = collect(client)

        // What arrived is kept.
        assertEquals("Half a sen", events.filterIsInstance<StreamEvent.Chunk>().single().text)

        // And the flow ends rather than hanging - with no Complete and no
        // Failed. ChatViewModel.streamReply settles the bubble after
        // collection for exactly this shape; relying on the Complete branch
        // alone would leave the send button spinning forever.
        assertFalse(events.any { it is StreamEvent.Complete })
        assertFalse(events.any { it is StreamEvent.Failed })
    }

    @Test
    fun `an unconfigured server fails without opening a socket`() {

        val client = AuraStreamClient(FakeSettings(serverUrl = ""))

        assertEquals(
            AuraError.NotConfigured,
            (collect(client).single() as StreamEvent.Failed).error,
        )
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `an unparseable frame does not crash the stream`() {

        val client = clientFor { socket ->
            socket.send("this is not json at all")
        }

        assertTrue(collect(client).last() is StreamEvent.Failed)
    }

    private companion object {
        /** Generous: this is a real socket, but it must not hang a build. */
        const val SOCKET_TIMEOUT = 10_000L
    }
}
