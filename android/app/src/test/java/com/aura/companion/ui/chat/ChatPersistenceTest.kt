package com.aura.companion.ui.chat

import androidx.lifecycle.viewModelScope
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.chat.Author
import com.aura.companion.data.chat.StoredConversation
import com.aura.companion.data.chat.StoredMessage
import com.aura.companion.data.chat.Transcript
import com.aura.companion.data.settings.FakeSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * §15: "AURA's chat UI must NOT lose visible history when the application
 * closes."
 *
 * Closing the app is not something a unit test can do, so what is tested is
 * the seam it acts through: a [ChatViewModel] built over a [Transcript] that
 * already holds a conversation must show it, and one that is given messages
 * must leave them somewhere a later ViewModel would find them. Building a
 * second ViewModel over the same fake store is as close to a relaunch as
 * this level gets, and it is close enough to catch the two bugs that matter
 * - nothing is loaded, and nothing is saved.
 *
 * The interesting cases here are the ones where saving is *wrong*. A store
 * that faithfully mirrors the screen would erase the transcript on the first
 * launch after a read failure, because a failed read looks exactly like an
 * empty conversation from one frame away. §41 - "Do not destroy existing
 * data" - is the reason several of these tests assert that nothing happened.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ChatPersistenceTest {

    private lateinit var server: MockWebServer

    private val viewModels = mutableListOf<ChatViewModel>()

    @Before
    fun setUp() {
        // Same reasoning as ChatViewModelTest: the work is on OkHttp's
        // threads, so a virtual clock buys nothing.
        Dispatchers.setMain(Dispatchers.Unconfined)

        server = MockWebServer()

        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                return when {
                    path.startsWith("/api/chat/stream") -> MockResponse().setResponseCode(404)
                    path.startsWith("/api/chat") -> MockResponse().setResponseCode(200).setBody(
                        """{"session_id":"s-rest","reply":"ừ tớ hiểu rồi","message_id":"m-1"}"""
                    )
                    path.startsWith("/api/health") -> MockResponse().setResponseCode(200).setBody(
                        """{"status":"ok","version":"0.1.0","uptime_seconds":1.0,
                           "runtime":{"llm_provider":"ollama","memory":"sqlite"}}"""
                    )
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }

        server.start()
    }

    /** Cancel the scopes, then the server, then the dispatcher. */
    @After
    fun tearDown() {

        runBlocking {
            viewModels.forEach { viewModel ->
                val job = viewModel.viewModelScope.coroutineContext[Job] ?: return@forEach
                withTimeoutOrNull(TIMEOUT_MS) { job.cancelAndJoin() }
                    ?: throw AssertionError("a ViewModel's coroutines did not stop")
            }
        }

        viewModels.clear()

        if (::server.isInitialized) server.shutdown()

        Dispatchers.resetMain()
    }

    // ------------------------------------------------------------------
    // Loading
    // ------------------------------------------------------------------

    @Test
    fun `a stored conversation is on screen before anything is sent`() {

        val store = FakeTranscript(
            StoredConversation(
                listOf(
                    StoredMessage("1", "hôm qua tớ hỏi gì ấy nhỉ", Author.USER, 1L),
                    StoredMessage("2", "cậu hỏi về pytest", Author.AURA, 2L),
                )
            )
        )

        val state = viewModel(store).state.value

        assertEquals(
            listOf("hôm qua tớ hỏi gì ấy nhỉ", "cậu hỏi về pytest"),
            state.messages.map { it.text }
        )
        assertEquals(ChatMessage.Author.USER, state.messages.first().author)
        assertEquals(ChatMessage.Author.AURA, state.messages.last().author)
    }

    @Test
    fun `a restored reply is not still arriving`() {

        // The process died mid-reply, so the last bubble was `streaming`
        // when it was last seen. Restoring that flag gives a bubble that
        // spins forever with no request behind it, and `isSending` would
        // never have been saved to match. The format has no field for it.
        val store = FakeTranscript(
            StoredConversation(listOf(StoredMessage("1", "đang trả lờ", Author.AURA, 1L)))
        )

        val state = viewModel(store).state.value

        assertFalse("a restored bubble must not be streaming", state.messages.single().streaming)
        assertFalse("a restored conversation is not mid-send", state.isSending)
    }

    @Test
    fun `a message that never sent comes back marked so the user can retry`() {

        val store = FakeTranscript(
            StoredConversation(
                listOf(StoredMessage("1", "gửi lúc mất mạng", Author.USER, 1L, failed = true))
            )
        )

        assertTrue(viewModel(store).state.value.messages.single().failed)
    }

    @Test
    fun `an empty store leaves the screen exactly as it was`() {

        val state = viewModel(FakeTranscript()).state.value

        assertTrue(state.messages.isEmpty())
        assertEquals("", state.draft)
    }

    // ------------------------------------------------------------------
    // Saving
    // ------------------------------------------------------------------

    @Test
    fun `what the user typed is stored without waiting for a reply`() {

        // The window this closes is the one that loses the most: a long
        // message typed on a bad link, the app swiped away while the
        // request is still out. Saving on reply would lose exactly the
        // message the user would be most annoyed to retype.
        val store = FakeTranscript()

        send(viewModel(store), "cậu giúp tớ sửa cái test này với")

        awaitStore(store, "the typed message to be stored") { conversation ->
            conversation.messages.any { it.text == "cậu giúp tớ sửa cái test này với" }
        }
    }

    @Test
    fun `the reply is stored too`() {

        val store = FakeTranscript()

        send(viewModel(store), "hello")

        awaitStore(store, "the reply to be stored") { conversation ->
            conversation.messages.any { it.author == Author.AURA }
        }

        assertEquals("ừ tớ hiểu rồi", store.current.messages.last { it.author == Author.AURA }.text)
    }

    @Test
    fun `a relaunch shows the conversation the last run left behind`() {

        // The whole of §15 in one test: two ViewModels, one store, nothing
        // in between. The second one is the app opening again.
        val store = FakeTranscript()

        send(viewModel(store), "trước khi tắt app")

        awaitStore(store, "the exchange to be stored") { it.messages.size >= 2 }

        val reopened = viewModel(store).state.value

        assertEquals("trước khi tắt app", reopened.messages.first().text)
        assertEquals(ChatMessage.Author.AURA, reopened.messages.last().author)
    }

    // ------------------------------------------------------------------
    // When not to write
    // ------------------------------------------------------------------

    @Test
    fun `a read that fails does not erase what it failed to read`() {

        // The bug this exists for: a store that mirrors the screen writes
        // whatever the screen holds, and after a failed read the screen
        // holds nothing. One unlucky launch and the transcript is gone -
        // and the user's next launch would show it gone too, so there is
        // no version of this the user could report as recoverable.
        val store = FakeTranscript(
            StoredConversation(listOf(StoredMessage("1", "keep me", Author.USER, 1L)))
        )
        store.failReads = true

        val state = viewModel(store).state.value

        assertTrue("a failed read shows nothing", state.messages.isEmpty())
        assertEquals("nothing may have been written", 0, store.writes)
        assertEquals("keep me", store.current.messages.single().text)
    }

    @Test
    fun `a launch does not write back what it just read`() {

        // The collector's first emission is the conversation that was just
        // restored, and `distinctUntilChanged` cannot recognise it as a
        // repeat - the first value it ever sees is new by definition. Without
        // a seeded comparison every launch would serialise and re-encrypt the
        // whole transcript to store exactly what was already on disk.
        //
        // This also pins an initialisation order: the field holding "what the
        // store already has" must be assigned before `init` starts the
        // collector, because on an unconfined dispatcher the collector runs
        // synchronously from `launch`.
        val store = FakeTranscript(
            StoredConversation(
                listOf(
                    StoredMessage("1", "cũ rồi", Author.USER, 1L),
                    StoredMessage("2", "ừ", Author.AURA, 2L),
                )
            )
        )

        val viewModel = viewModel(store)

        assertEquals("nothing changed, so nothing was written", 0, store.writes)
        assertEquals(2, viewModel.state.value.messages.size)
    }

    @Test
    fun `an empty screen is never written by itself`() {

        // Emptiness is only ever stored on purpose - by `newConversation`,
        // which calls `clear`. Nothing else may store it, because every
        // other route to an empty screen is a failure of some kind.
        val store = FakeTranscript()

        viewModel(store)

        assertEquals(0, store.writes)
        assertFalse(store.cleared)
    }

    @Test
    fun `starting a new conversation clears the store and not just the screen`() {

        // The docstring on `newConversation` used to say clearing the
        // messages was cosmetic. Once history is persisted it is the
        // opposite: leaving the store alone would bring the old
        // conversation back on the next launch, under a new session id the
        // server has no memory of.
        val store = FakeTranscript()
        val viewModel = viewModel(store)

        send(viewModel, "câu hỏi cũ")
        awaitStore(store, "the message to be stored") { it.messages.isNotEmpty() }

        viewModel.newConversation()

        assertTrue("the screen is cleared", viewModel.state.value.messages.isEmpty())
        assertTrue("the store is cleared", store.cleared)
        assertTrue("nothing is left to restore", store.current.messages.isEmpty())
    }

    // ------------------------------------------------------------------
    // Fakes and helpers
    // ------------------------------------------------------------------

    /**
     * A [Transcript] in memory, plus the two things the tests ask about it:
     * how many times it was written, and whether it was cleared.
     *
     * `writes` is counted rather than inferred from the contents because
     * "wrote the same thing again" and "did not write" are different
     * behaviours here - the first is a commit per token on a real store.
     */
    private class FakeTranscript(
        private var stored: StoredConversation = StoredConversation(),
    ) : Transcript {

        var failReads = false
        var writes = 0
        var cleared = false

        val current: StoredConversation get() = stored

        override fun read(): StoredConversation {
            if (failReads) throw IllegalStateException("keystore unavailable")
            return stored
        }

        override fun write(messages: List<StoredMessage>) {
            writes++
            stored = stored.copy(messages = messages)
        }

        override fun clear() {
            cleared = true
            stored = StoredConversation()
        }
    }

    private fun viewModel(transcript: Transcript): ChatViewModel {

        val settings = FakeSettings(serverUrl = server.url("/").toString(), authToken = "t")

        return ChatViewModel(AuraRepository(settings), settings, transcript)
            .also { viewModels += it }
    }

    private fun send(viewModel: ChatViewModel, text: String): ChatViewModel {
        viewModel.onDraftChanged(text)
        viewModel.send()
        return viewModel
    }

    /** Wait for the store to satisfy [predicate], or fail saying what for. */
    private fun awaitStore(
        store: FakeTranscript,
        what: String,
        predicate: (StoredConversation) -> Boolean,
    ) {
        val deadline = System.currentTimeMillis() + TIMEOUT_MS

        while (System.currentTimeMillis() < deadline) {
            if (predicate(store.current)) return
            Thread.sleep(10)
        }

        throw AssertionError("timed out waiting for $what; store holds ${store.current}")
    }

    private companion object {
        const val TIMEOUT_MS = 10_000L
    }
}
