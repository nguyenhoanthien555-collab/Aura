package com.aura.companion.ui.chat

import androidx.lifecycle.viewModelScope
import com.aura.companion.data.AuraError
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.settings.FakeSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * §21: the send lifecycle, over a real server on loopback.
 *
 * This is where the streaming-to-REST fallback lives, and its failure modes
 * are the ones a user actually reports: a reply that never settles leaves
 * the send button spinning with no way back, and a lost message loses what
 * they typed.
 *
 * `runBlocking` and a routing dispatcher, not `runTest` and a response
 * queue, for two independent reasons:
 *
 *  - [AuraRepository] does its work inside `withContext(Dispatchers.IO)` and
 *    the WebSocket runs on OkHttp's own threads. Neither is under a test
 *    scheduler's control, so `advanceUntilIdle` would return before the
 *    request had happened. These tests wait on the state itself instead,
 *    with a real-time timeout.
 *  - `init` calls `checkConnection`, which fires a health request. Against a
 *    queue that request would consume the response the test enqueued for
 *    chat. Routing by path removes the ordering dependency entirely.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChatViewModelTest {

    private lateinit var server: MockWebServer

    /**
     * What each route answers. Set by a test before it builds the
     * ViewModel, because construction already talks to `/api/health`.
     */
    private var healthRoute: () -> MockResponse = { healthOk() }
    private var chatRoute: () -> MockResponse = { chatOk("Hello from REST.") }
    private var streamRoute: () -> MockResponse = { MockResponse().setResponseCode(404) }

    /**
     * Every ViewModel a test built, so teardown can stop it.
     *
     * On a phone this is the `ViewModelStore`'s job. Here nothing owns them,
     * and several of these tests deliberately leave work running - a stream
     * that never terminates is the point of one of them. See [tearDown].
     */
    private val viewModels = mutableListOf<ChatViewModel>()

    @Before
    fun setUp() {

        // Unconfined rather than a scheduler-backed TestDispatcher: the work
        // being waited on happens on OkHttp's threads, so a virtual clock
        // buys nothing and a paused dispatcher would only hide the handoff.
        Dispatchers.setMain(Dispatchers.Unconfined)

        server = MockWebServer()

        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {

                val path = request.path.orEmpty()

                // Longest prefix first: /api/chat/stream also starts with
                // /api/chat.
                return when {
                    path.startsWith("/api/chat/stream") -> streamRoute()
                    path.startsWith("/api/chat") -> chatRoute()
                    path.startsWith("/api/health") -> healthRoute()
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }

        server.start()
    }

    /**
     * Stop the ViewModels, then the server, then the dispatcher.
     *
     * WHY THIS IS NOT JUST TWO LINES
     * ------------------------------
     * It was, and it made the whole Android suite flaky: roughly one run in
     * three failed somewhere in this class with
     * `IllegalStateException: Dispatchers.Main is used concurrently with
     * setting it`, sometimes from `resetMain` here and sometimes from
     * `setMain` in the *next* test's [setUp].
     *
     * `Dispatchers.setMain`/`resetMain` guard against being written while
     * something is reading `Dispatchers.Main`, and `viewModelScope` reads it
     * on every dispatch. Nothing here ever stopped the ViewModels, so a test
     * that left a coroutine in flight - the never-terminating stream, or the
     * health probe `init` fires - handed a live reader to the write below.
     * Whether it blew up depended on thread timing, which is why adding
     * unrelated test classes to the fork changed which test failed.
     *
     * The order is deliberate:
     *
     *  1. Cancel and *join* each scope. Cancellation is a request; joining
     *     is what makes "no reader is left" a fact rather than a hope.
     *     [AuraStreamClient][com.aura.companion.data.remote.AuraStreamClient]
     *     closes its socket in `awaitClose`, so this unwinds promptly.
     *  2. Then shut the server down. The other way round, `shutdown()` waits
     *     up to five seconds for an open WebSocket's queue to go idle and
     *     then throws.
     *  3. Then release `Dispatchers.Main`.
     *
     * The `isInitialized` guard is for the case where [setUp] itself threw:
     * without it the real failure is buried under a `lateinit` error from
     * teardown.
     */
    @After
    fun tearDown() {

        stopViewModels()

        if (::server.isInitialized) server.shutdown()

        Dispatchers.resetMain()
    }

    /** Cancel every ViewModel's scope and wait for it to actually finish. */
    private fun stopViewModels() = runBlocking {

        viewModels.forEach { viewModel ->

            val job = viewModel.viewModelScope.coroutineContext[Job] ?: return@forEach

            // Real time: what is being waited on is a socket unwinding, not
            // a virtual clock. A timeout here is a genuine leak, not a slow
            // machine, so it fails loudly rather than leaving the next test
            // to inherit the coroutine.
            withTimeoutOrNull(TIMEOUT_MS) { job.cancelAndJoin() }
                ?: throw AssertionError(
                    "a ViewModel's coroutines did not stop within ${TIMEOUT_MS}ms"
                )
        }

        viewModels.clear()
    }

    // ------------------------------------------------------------------
    // Streaming
    // ------------------------------------------------------------------

    @Test
    fun `a streamed reply grows one bubble and settles it`() {

        streamRoute = {
            streaming { socket ->
                socket.send("""{"type":"started","session_id":"s-1","message_id":"m-1"}""")
                socket.send("""{"type":"chunk","chunk":"Hello","index":0}""")
                socket.send("""{"type":"chunk","chunk":", there.","index":1}""")
                socket.send(
                    """{"type":"complete","session_id":"s-1","message_id":"m-1",
                       "total_chunks":2,"elapsed_seconds":1.0}"""
                )
            }
        }

        val viewModel = send(viewModel(), "hello")

        val state = await(viewModel, "the reply to settle") { settled(it) }

        // One bubble that grew, not one bubble per chunk.
        val reply = aura(state).single()
        assertEquals("Hello, there.", reply.text)

        // Both flags cleared, or the send button never comes back.
        assertFalse("bubble still marked streaming", reply.streaming)
        assertFalse("still sending", state.isSending)
        assertNull(state.error)

        // The user's message is kept and not marked failed.
        val sent = state.messages.first { it.author == ChatMessage.Author.USER }
        assertEquals("hello", sent.text)
        assertFalse(sent.failed)
    }

    @Test
    fun `a socket that closes mid-reply still settles the bubble`() {

        // The bug this guards: the terminal frame never arrives - a proxy
        // timing out mid-generation does exactly this - so nothing in the
        // `complete` branch ever runs. If settling depended on that branch,
        // `isSending` would stay set and the conversation would be frozen
        // with no error and no way to retry.
        streamRoute = {
            streaming { socket ->
                socket.send("""{"type":"started","session_id":"s-1","message_id":"m-1"}""")
                socket.send("""{"type":"chunk","chunk":"Half a sen","index":0}""")
                socket.close(1000, null)
            }
        }

        val viewModel = send(viewModel(), "hello")

        val state = await(viewModel, "the truncated reply to settle") { settled(it) }

        val reply = aura(state).single()

        // What did arrive is kept: a partial answer beats a blank screen.
        assertEquals("Half a sen", reply.text)
        assertFalse("bubble still marked streaming", reply.streaming)
        assertFalse("still sending", state.isSending)
    }

    @Test
    fun `a stream that fails after text is reported rather than re-asked`() {

        streamRoute = {
            streaming { socket ->
                socket.send("""{"type":"chunk","chunk":"Partly","index":0}""")
                socket.send("""{"type":"error","error":"stream_failed"}""")
            }
        }

        val viewModel = send(viewModel(), "hello")

        val state = await(viewModel, "the failure to surface") { it.error != null }

        assertEquals(AuraError.ServerFailure(500), state.error)

        // Exactly one reply. Falling back here would put the same question
        // to the model twice and show the user two answers to it.
        assertEquals("Partly", aura(state).single().text)
        assertEquals(0, chatRequests())
    }

    // ------------------------------------------------------------------
    // Falling back to REST
    // ------------------------------------------------------------------

    @Test
    fun `a refused WebSocket falls back to REST and still answers`() {

        // A proxy that will not carry an upgrade. That is a deployment
        // property, not something the person typing can fix, so it must not
        // reach them as an error.
        streamRoute = { MockResponse().setResponseCode(404) }
        chatRoute = { chatOk("Answered over REST.") }

        val viewModel = send(viewModel(), "hello")

        val state = await(viewModel, "the REST reply") { settled(it) }

        assertEquals("Answered over REST.", aura(state).single().text)
        assertFalse(state.isSending)

        // Silent by design: the only visible difference is that the reply
        // arrived whole instead of growing.
        assertNull(state.error)

        assertEquals(1, chatRequests())
    }

    @Test
    fun `a stream that opens but says nothing still falls back`() {

        // Worse than a refusal: the handshake succeeds, so nothing failed -
        // the socket simply closes with no frames. Keying the fallback on
        // "no text arrived" rather than "the socket errored" is what covers
        // this.
        streamRoute = { streaming { socket -> socket.close(1000, null) } }
        chatRoute = { chatOk("Still here.") }

        val viewModel = send(viewModel(), "hello")

        val state = await(viewModel, "the REST reply") { settled(it) }

        assertEquals("Still here.", aura(state).single().text)
        assertFalse(state.isSending)
    }

    // ------------------------------------------------------------------
    // Failure
    // ------------------------------------------------------------------

    @Test
    fun `a failed send keeps the message and offers it back`() {

        streamRoute = { MockResponse().setResponseCode(404) }
        chatRoute = { MockResponse().setResponseCode(500).setBody("""{"error":"chat_failed"}""") }

        val viewModel = send(viewModel(), "a long message worth not losing")

        val state = await(viewModel, "the failure") { it.error != null }

        val sent = state.messages.single { it.author == ChatMessage.Author.USER }

        // Kept, not dropped. On a mobile link this is the difference between
        // an app you trust with a long message and one you do not.
        assertEquals("a long message worth not losing", sent.text)
        assertTrue("the message should be marked failed", sent.failed)

        assertEquals(AuraError.ServerFailure(500), state.error)
        assertFalse("still sending after a failure", state.isSending)
        assertTrue(state.connection is ConnectionState.Unavailable)
    }

    @Test
    fun `retry resends a failed message without retyping it`() {

        streamRoute = { MockResponse().setResponseCode(404) }
        chatRoute = { MockResponse().setResponseCode(500) }

        val viewModel = send(viewModel(), "try again please")

        val failed = await(viewModel, "the failure") { it.error != null }
            .messages.single { it.failed }

        chatRoute = { chatOk("Got it this time.") }

        viewModel.retry(failed.id)

        val state = await(viewModel, "the retried reply") { aura(it).isNotEmpty() }

        assertEquals("Got it this time.", aura(state).single().text)

        // One user message, not two: the failed one was replaced rather
        // than left behind above its own retry.
        assertEquals(1, state.messages.count { it.author == ChatMessage.Author.USER })
        assertFalse(state.messages.any { it.failed })
    }

    @Test
    fun `a rejected token is surfaced rather than silently retried`() {

        streamRoute = { MockResponse().setResponseCode(401) }
        chatRoute = { MockResponse().setResponseCode(401) }

        val viewModel = send(viewModel(), "hello")

        val state = await(viewModel, "the auth failure") { it.error != null }

        // Unlike a cold start, this needs the user to go and change
        // something, so it must not be hidden behind a fallback.
        assertEquals(AuraError.Unauthorized, state.error)
    }

    // ------------------------------------------------------------------
    // Input handling
    // ------------------------------------------------------------------

    @Test
    fun `sending clears the draft immediately`() {

        streamRoute = { streaming { socket -> socket.close(1000, null) } }

        val viewModel = viewModel()

        viewModel.onDraftChanged("hello")
        viewModel.send()

        // Cleared synchronously, before the network is involved: leaving it
        // in the box invites a second tap that sends the same thing twice.
        assertEquals("", viewModel.state.value.draft)
    }

    @Test
    fun `an empty or whitespace draft is not sent`() {

        val viewModel = viewModel()

        viewModel.onDraftChanged("   \n  ")
        viewModel.send()

        assertTrue(viewModel.state.value.messages.isEmpty())
        assertEquals(0, chatRequests())
    }

    @Test
    fun `nothing is sent while a reply is still arriving`() {

        streamRoute = {
            streaming { socket ->
                socket.send("""{"type":"chunk","chunk":"thinking","index":0}""")
                // Deliberately never terminates within the test.
            }
        }

        val viewModel = send(viewModel(), "first")

        await(viewModel, "the first chunk") { it.messages.size >= 2 }

        viewModel.onDraftChanged("second")
        viewModel.send()

        // Still the one user message. Allowing a second would interleave two
        // turns in one conversation.
        assertEquals(1, viewModel.state.value.messages.count {
            it.author == ChatMessage.Author.USER
        })
        assertEquals("second", viewModel.state.value.draft)
    }

    @Test
    fun `an unconfigured server refuses to send and says why`() {

        val viewModel = viewModel(serverUrl = "")

        viewModel.onDraftChanged("hello")
        viewModel.send()

        assertEquals(AuraError.NotConfigured, viewModel.state.value.error)
        assertTrue(viewModel.state.value.messages.isEmpty())
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `with no accessibility service attached nothing is asked about routing`() {

        // Routing costs a server round-trip, so it is only worth asking
        // when there is something that could act on the answer. There is
        // no service in a JVM test, so exactly one chat request should
        // leave: the message itself, and no intent probe ahead of it.
        streamRoute = { MockResponse().setResponseCode(404) }
        chatRoute = { chatOk("Hey.") }

        val viewModel = viewModel()

        await(viewModel, "the connection banner") {
            it.connection is ConnectionState.Connected
        }

        viewModel.onDraftChanged("hello")
        viewModel.send()

        await(viewModel, "the reply") { state ->
            state.messages.any { it.author == ChatMessage.Author.AURA }
        }

        val paths = buildList {
            repeat(server.requestCount) {
                add(
                    server.takeRequest(1, java.util.concurrent.TimeUnit.MILLISECONDS)
                        ?.path.orEmpty()
                )
            }
        }

        assertEquals(1, paths.count { it.startsWith("/api/chat") && !it.startsWith("/api/chat/stream") })
    }

    // ------------------------------------------------------------------
    // Connection
    // ------------------------------------------------------------------

    @Test
    fun `a healthy server names its provider in the banner`() {

        val viewModel = viewModel()

        val state = await(viewModel, "the connection banner") {
            it.connection is ConnectionState.Connected
        }

        assertEquals("ollama", (state.connection as ConnectionState.Connected).provider)
    }

    @Test
    fun `an unreachable server is reported without an error banner`() {

        healthRoute = { MockResponse().setResponseCode(502) }

        val viewModel = viewModel()

        val state = await(viewModel, "the unavailable banner") {
            it.connection is ConnectionState.Unavailable
        }

        // A failing probe is not worth a dismissable error on its own - the
        // connection line already says it, and Aura may well be waking.
        assertNull(state.error)
    }

    @Test
    fun `a probe rejected on the token is promoted to an error`() {

        healthRoute = { MockResponse().setResponseCode(401) }

        val viewModel = viewModel()

        // The exception: this one needs the user to go and fix something,
        // so the banner alone is not enough.
        assertEquals(
            AuraError.Unauthorized,
            await(viewModel, "the auth error") { it.error != null }.error,
        )
    }

    // ------------------------------------------------------------------
    // Conversation management
    // ------------------------------------------------------------------

    @Test
    fun `a new conversation clears the transcript`() {

        streamRoute = { MockResponse().setResponseCode(404) }

        val viewModel = send(viewModel(), "hello")

        await(viewModel, "the reply") { settled(it) }

        viewModel.newConversation()

        assertTrue(viewModel.state.value.messages.isEmpty())
        assertNull(viewModel.state.value.error)
    }

    @Test
    fun `a companion message tapped from a notification appears once`() {

        val viewModel = viewModel()

        viewModel.showCompanionMessage("You have been at this a while.")
        viewModel.showCompanionMessage("You have been at this a while.")

        // Tapping the notification twice, or tapping it and then rotating,
        // must not double the message.
        assertEquals(1, viewModel.state.value.messages.size)
        assertEquals(
            "You have been at this a while.",
            viewModel.state.value.messages.single().text,
        )
    }

    @Test
    fun `a blank companion message is ignored`() {

        val viewModel = viewModel()

        viewModel.showCompanionMessage("")
        viewModel.showCompanionMessage("   ")

        assertTrue(viewModel.state.value.messages.isEmpty())
    }

    @Test
    fun `dismissing an error leaves the conversation intact`() {

        streamRoute = { MockResponse().setResponseCode(404) }
        chatRoute = { MockResponse().setResponseCode(500) }

        val viewModel = send(viewModel(), "hello")

        await(viewModel, "the failure") { it.error != null }

        viewModel.dismissError()

        assertNull(viewModel.state.value.error)
        assertTrue(viewModel.state.value.messages.single().failed)
    }

    // ------------------------------------------------------------------
    // Fixtures
    // ------------------------------------------------------------------

    private fun viewModel(serverUrl: String = server.url("/").toString()): ChatViewModel {

        val settings = FakeSettings(serverUrl = serverUrl, authToken = "test-token")

        // Registered so [tearDown] can stop it. Every test builds its
        // ViewModel through here, which is what makes that guarantee hold.
        return ChatViewModel(AuraRepository(settings), settings)
            .also { viewModels += it }
    }

    private fun send(viewModel: ChatViewModel, text: String): ChatViewModel {
        viewModel.onDraftChanged(text)
        viewModel.send()
        return viewModel
    }

    /** A WebSocket route that runs [script] once the client's frame lands. */
    private fun streaming(script: (WebSocket) -> Unit) = MockResponse().withWebSocketUpgrade(
        object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                script(webSocket)
                // Mirrors server/routes/ws_chat.py, which closes in finally.
                webSocket.close(1000, null)
            }
        }
    )

    private fun healthOk() = MockResponse().setResponseCode(200).setBody(
        """{"status":"ok","version":"0.1.0","uptime_seconds":1.0,
           "runtime":{"llm_provider":"ollama","memory":"sqlite"}}"""
    )

    private fun chatOk(reply: String) = MockResponse().setResponseCode(200).setBody(
        """{"session_id":"s-rest","reply":"$reply","message_id":"m-rest"}"""
    )

    /**
     * How many REST chat requests the server saw.
     *
     * Counted from the recorded requests rather than a queue, because the
     * dispatcher answers health and stream on the same server and the point
     * of several of these tests is that REST was *not* used.
     */
    private fun chatRequests(): Int {

        var count = 0

        repeat(server.requestCount) {
            val path = server.takeRequest(1, java.util.concurrent.TimeUnit.MILLISECONDS)
                ?.path
                .orEmpty()
            if (path.startsWith("/api/chat") && !path.startsWith("/api/chat/stream")) {
                count++
            }
        }

        return count
    }

    /**
     * Wait until the state satisfies [predicate].
     *
     * Real time, because the thing being waited on is a real socket. The
     * timeout is generous enough never to flake and short enough never to
     * hang a build.
     */
    private fun await(
        viewModel: ChatViewModel,
        what: String,
        predicate: (ChatUiState) -> Boolean,
    ): ChatUiState = runBlocking {
        try {
            withTimeout(TIMEOUT_MS) { viewModel.state.first(predicate) }
        } catch (e: TimeoutCancellationException) {
            throw AssertionError(
                "timed out waiting for $what; state was ${viewModel.state.value}"
            )
        }
    }

    private fun aura(state: ChatUiState) =
        state.messages.filter { it.author == ChatMessage.Author.AURA }

    /** A turn is over: the send finished and something is on screen. */
    private fun settled(state: ChatUiState) =
        !state.isSending && state.messages.any { it.author == ChatMessage.Author.AURA }

    private companion object {
        const val TIMEOUT_MS = 10_000L
    }
}
