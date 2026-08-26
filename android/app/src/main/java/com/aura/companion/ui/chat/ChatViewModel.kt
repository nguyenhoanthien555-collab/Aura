package com.aura.companion.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.aura.companion.accessibility.AuraAccessibilityService
import com.aura.companion.accessibility.IntentRouter
import com.aura.companion.data.AuraError
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.AuraResult
import com.aura.companion.data.chat.Transcript
import com.aura.companion.data.remote.StreamEvent
import com.aura.companion.data.settings.SettingsProvider
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.util.UUID

/**
 * The chat screen's brain.
 *
 * Holds the conversation, drives the connection banner, and turns an
 * [AuraError] into something a person can act on. It never touches HTTP -
 * that is the repository's job - and it never formats a Composable.
 *
 * The one subtlety worth stating: a message the user typed is added to the
 * list *before* the request goes out, and marked `failed` if the request
 * fails. Dropping it on failure loses what they wrote, which on a mobile
 * link with intermittent signal is the difference between an app you trust
 * with a long message and one you don't.
 */
class ChatViewModel(
    private val repository: AuraRepository,
    private val settings: SettingsProvider,
    private val transcript: Transcript = Transcript.None,
) : ViewModel() {

    private val _state = MutableStateFlow(
        ChatUiState(
            messages = restored(),
            isConfigured = settings.current.isConfigured,
        )
    )
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    private var probe: Job? = null

    /**
     * What the store already holds, so a launch does not rewrite it.
     *
     * Seeded with the restored conversation. Without this the collector's
     * first emission is whatever was just read, and every launch would
     * serialise and re-encrypt two hundred messages to store exactly what was
     * already there. `distinctUntilChanged` cannot see that, because the
     * first value it sees is by definition not a repeat.
     */
    private var kept: List<ChatMessage> = _state.value.messages

    init {
        viewModelScope.launch {
            settings.settings.collect { current ->
                _state.update { it.copy(isConfigured = current.isConfigured) }
            }
        }
        keep()
        checkConnection()
    }

    // ------------------------------------------------------------------
    // History (§15)
    // ------------------------------------------------------------------

    /**
     * The conversation the last run left behind.
     *
     * In the initial state rather than loaded from `init`, so the first frame
     * the screen ever draws already has it. Loading a moment later would show
     * an empty conversation that filled in - which reads as a lost transcript
     * for exactly as long as anyone notices.
     *
     * A read that fails is an empty conversation and nothing more. An
     * exception here would be an app that will not open, and what the person
     * holding the phone can do about a Keystore that has become unavailable
     * is nothing.
     */
    private fun restored(): List<ChatMessage> = try {
        transcript.read().messages.map { it.rendered() }
    } catch (error: Exception) {
        emptyList()
    }

    /**
     * Keep the store in step with the screen.
     *
     * One collector rather than a save at each of the five places a message
     * is added, because the five would become six and the sixth would be the
     * one that forgot.
     *
     * TWO THINGS IT WILL NOT WRITE
     * ----------------------------
     * *A reply that is still arriving.* Excluding it from the projection is
     * not only about the flag - it is what stops a save per token. A real
     * store rewrites its whole file on commit, so mirroring the screen
     * literally would mean a file write for every few characters Aura says.
     * With the growing bubble excluded the projection is constant for the
     * whole of a streamed reply, and the write happens once, when it settles.
     *
     * *Emptiness.* Every route to an empty screen other than
     * [newConversation] is a failure of some kind - most sharply a read that
     * threw, which leaves the screen empty while the file it could not read
     * is still there. Writing that back would destroy the transcript rather
     * than fail to load it, and §41 is explicit that existing data is not
     * ours to destroy. So emptiness is only ever stored on purpose, by
     * [newConversation] calling `clear`.
     */
    private fun keep() {
        viewModelScope.launch {
            state
                .map { current -> current.messages.filterNot { it.streaming } }
                .distinctUntilChanged()
                .collect { messages ->

                    if (messages.isEmpty() || messages == kept) return@collect

                    try {
                        transcript.write(messages.map { it.stored() })
                        kept = messages
                    } catch (error: Exception) {
                        // The conversation on screen is unaffected, and `kept`
                        // is left alone so the next change tries again. Losing
                        // the transcript at the next launch is bad; losing this
                        // turn to a failed write would be worse.
                    }
                }
        }
    }

    // ------------------------------------------------------------------
    // Input
    // ------------------------------------------------------------------

    fun onDraftChanged(text: String) {
        _state.update { it.copy(draft = text) }
    }

    fun dismissError() {
        _state.update { it.copy(error = null) }
    }

    /**
     * Start a fresh conversation.
     *
     * Clears the session id, so the server allocates a new one and does not
     * carry the old context forward, and clears the stored transcript.
     *
     * Clearing the messages was once cosmetic - the session id was the only
     * thing that survived the tap. Since §15 it is the opposite: leaving the
     * store alone would bring the old conversation back at the next launch,
     * sitting under a session id the server has never heard of. This is also
     * the only place emptiness is ever written, which is why `keep` refuses
     * to write it and this says so explicitly.
     */
    fun newConversation() {

        repository.resetSession()

        try {
            transcript.clear()
            kept = emptyList()
        } catch (error: Exception) {
            // Nothing to do but carry on with a fresh screen.
        }

        _state.update { it.copy(messages = emptyList(), error = null) }
    }

    // ------------------------------------------------------------------
    // Connection
    // ------------------------------------------------------------------

    /**
     * Probe the server and update the banner.
     *
     * The "waking up" state is time-based rather than error-based: a
     * suspended free-tier service accepts the TCP connection immediately
     * and then holds the request open while the container starts, so
     * nothing fails - it just takes a long time. After four seconds
     * without an answer we say so, because a silent spinner reads as a
     * broken app.
     */
    fun checkConnection() {

        if (!settings.current.isConfigured) {
            _state.update {
                it.copy(connection = ConnectionState.Unavailable("Not configured"))
            }
            return
        }

        probe?.cancel()

        probe = viewModelScope.launch {

            _state.update { it.copy(connection = ConnectionState.Connecting) }

            val slowNotice = launch {
                delay(WAKE_NOTICE_MS)
                if (isActive) {
                    _state.update { it.copy(connection = ConnectionState.WakingUp) }
                }
            }

            when (val result = repository.health()) {

                is AuraResult.Ok -> {
                    slowNotice.cancel()
                    _state.update {
                        it.copy(
                            connection = ConnectionState.Connected(
                                result.value.runtime["llm_provider"] ?: "aura"
                            ),
                            error = null,
                        )
                    }
                }

                is AuraResult.Failed -> {
                    slowNotice.cancel()
                    _state.update {
                        it.copy(
                            connection = ConnectionState.Unavailable(
                                result.error.userMessage
                            ),
                            // A failing probe is not worth an error banner on
                            // its own; the connection line already says it.
                            // Only an auth failure gets promoted, because it
                            // needs the user to go and fix something - and a
                            // 403 needs that as much as a 401 does, even
                            // though what needs fixing is not the same.
                            error = if (
                                result.error is AuraError.Unauthorized ||
                                result.error is AuraError.Forbidden
                            ) {
                                result.error
                            } else {
                                it.error
                            },
                        )
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Sending
    // ------------------------------------------------------------------

    fun send() {

        val text = _state.value.draft.trim()

        if (text.isEmpty() || _state.value.isSending) return

        if (!settings.current.isConfigured) {
            _state.update { it.copy(error = AuraError.NotConfigured) }
            return
        }

        val outgoing = ChatMessage(
            id = UUID.randomUUID().toString(),
            text = text,
            author = ChatMessage.Author.USER,
        )

        _state.update {
            it.copy(
                messages = it.messages + outgoing,
                draft = "",
                isSending = true,
                error = null,
            )
        }

        viewModelScope.launch {

            // Only a message that actually asks for something to happen
            // on the phone goes into the agent loop. Deciding this needs
            // a server round-trip, so it happens here rather than in
            // `send` itself, which is not suspending.
            if (wantsDeviceAction(text) && startAgentTask(text)) {
                return@launch
            }

            val slowNotice = launch {
                delay(WAKE_NOTICE_MS)
                if (isActive) {
                    _state.update { it.copy(connection = ConnectionState.WakingUp) }
                }
            }

            // The phone knows which app is in front of the owner; the
            // server does not unless this message says so. Built once per
            // turn and used by whichever transport answers.
            val context = conversationContext()

            val streamed = streamReply(text, slowNotice, context)

            // Falling back rather than reporting a failure: a proxy that
            // will not carry a WebSocket is a deployment property, not
            // something the person typing can fix. REST answers the same
            // question with the same session, so the conversation
            // continues and the only visible difference is that the reply
            // arrives whole instead of growing.
            if (!streamed) {
                sendOverRest(outgoing.id, text, slowNotice, context)
            }
        }
    }

    /**
     * What this phone can tell the server about the screen, per message.
     *
     * Two halves, both honest:
     *
     *  - `app`: the foreground package/label/activity from the
     *    accessibility layer. This is metadata, not observation - it rides
     *    with a message the owner deliberately sent, and answers "app gì
     *    vậy?" without any pixels. Screen *text* still travels only
     *    through [com.aura.companion.screen.ScreenObservationService]
     *    behind its own switch; nothing here becomes a second, ungated
     *    copy of that stream.
     *
     *  - `screen_note`: one sentence, written on the phone, about what
     *    this phone's screen pipeline cannot do right now - quoted by the
     *    server verbatim rather than re-derived. When pixels flow, there
     *    is no note: absence of bad news means there is no bad news.
     *
     * Empty when the accessibility service is not connected, so an older
     * or un-permissioned install sends exactly what it always sent.
     */
    private fun conversationContext(): JsonObject {

        val app = AuraAccessibilityService.currentForegroundApp()
            ?: return JsonObject(emptyMap())

        val entries = mutableMapOf<String, JsonElement>(
            "app" to JsonObject(
                buildMap {
                    put("package", JsonPrimitive(app.packageName))
                    if (app.label.isNotBlank()) put("label", JsonPrimitive(app.label))
                    app.activity?.takeIf { it.isNotBlank() }?.let {
                        put("activity", JsonPrimitive(it))
                    }
                }
            )
        )

        screenNote()?.let { entries["screen_note"] = JsonPrimitive(it) }

        return JsonObject(entries)
    }

    /**
     * Why "look at my screen" cannot mean pixels right now, or nothing.
     *
     * Mirrors the gates [com.aura.companion.screen.ScreenshotUploader]
     * applies, in the same order, so the sentence the server quotes is
     * the reason the uploader would have given.
     */
    private fun screenNote(): String? = when {
        !settings.current.screenObservationEnabled ->
            "Screen observation is switched off on this phone, so Aura " +
                "cannot read what is on screen beyond the foreground app."
        !settings.current.uploadScreenshots ->
            "Screenshot upload is switched off on this phone, so Aura " +
                "cannot see the screen's pixels."
        else -> null
    }

    /**
     * Should this message drive the phone rather than be answered?
     *
     * False whenever there is no accessibility service to drive it, and
     * false whenever the routing question itself could not be answered.
     * A probe that fails means the server is unreachable, and the normal
     * path reports that honestly - starting a device loop against a
     * server the loop is about to need would fail later and less
     * clearly.
     */
    private suspend fun wantsDeviceAction(text: String): Boolean {

        if (!AuraAccessibilityService.isEnabled()) return false

        return when (val result = repository.send(text, IntentRouter.PROBE_CONTEXT)) {
            is AuraResult.Ok -> IntentRouter.isAction(result.value.reply)
            is AuraResult.Failed -> false
        }
    }

    /**
     * Hand the request to the accessibility agent.
     *
     * False if the service went away between the check and here - it can
     * be switched off in Settings mid-turn - in which case the caller
     * falls through to the conversational path and the user gets an
     * answer instead of silence.
     */
    private fun startAgentTask(text: String): Boolean =

        AuraAccessibilityService.startAgentTask(text) { finalReply ->
            _state.update { current ->
                current.copy(
                    messages = current.messages + ChatMessage(
                        id = UUID.randomUUID().toString(),
                        text = finalReply,
                        author = ChatMessage.Author.AURA,
                    ),
                    isSending = false,
                )
            }
        }

    /**
     * Stream the reply, returning false if the socket never delivered one.
     *
     * False means "nothing usable arrived" - the socket failed before any
     * text did. Once a chunk has been rendered the turn belongs to
     * streaming, so a failure after that is reported rather than retried:
     * re-sending would ask the model the same question twice and show the
     * user two answers.
     */
    private suspend fun streamReply(
        message: String,
        slowNotice: Job,
        context: JsonObject = JsonObject(emptyMap()),
    ): Boolean {

        val messageId = UUID.randomUUID().toString()

        var reply = ""

        var failure: AuraError? = null

        repository.stream(message, context).collect { event ->

            when (event) {

                is StreamEvent.Started -> {
                    slowNotice.cancel()
                }

                is StreamEvent.Chunk -> {

                    val first = reply.isEmpty()

                    reply += event.text

                    _state.update { current ->
                        current.copy(
                            messages = if (first) {
                                current.messages + ChatMessage(
                                    id = messageId,
                                    text = reply,
                                    author = ChatMessage.Author.AURA,
                                    streaming = true,
                                )
                            } else {
                                current.messages.map {
                                    if (it.id == messageId) it.copy(text = reply) else it
                                }
                            },
                            connection = ConnectionState.Connected(
                                (current.connection as? ConnectionState.Connected)
                                    ?.provider ?: "aura"
                            ),
                        )
                    }
                }

                is StreamEvent.Complete -> {
                    _state.update { current ->
                        current.copy(
                            messages = current.messages.map {
                                if (it.id == messageId) it.copy(streaming = false) else it
                            },
                            isSending = false,
                        )
                    }
                }

                is StreamEvent.Failed -> {
                    failure = event.error
                }
            }
        }

        slowNotice.cancel()

        // Nothing arrived: let the caller try the REST path.
        if (reply.isEmpty()) return false

        // Text arrived, so this turn is streaming's to finish. Settle the
        // bubble here rather than only in the Complete branch: a socket can
        // close without a terminal frame - a proxy timing out mid-reply
        // does exactly that - and a `streaming` flag left set is the send
        // button spinning forever with no way back.
        //
        // Any failure after the first chunk is reported, not retried.
        // Re-asking would put the same question to the model twice and show
        // the user two answers to it.
        val error = failure

        _state.update { current ->
            current.copy(
                messages = current.messages.map {
                    if (it.id == messageId) it.copy(streaming = false) else it
                },
                isSending = false,
                error = error ?: current.error,
            )
        }

        return true
    }

    private suspend fun sendOverRest(
        outgoingId: String,
        text: String,
        slowNotice: Job,
        context: JsonObject = JsonObject(emptyMap()),
    ) {

        when (val result = repository.send(text, context)) {

            is AuraResult.Ok -> {
                slowNotice.cancel()
                _state.update { current ->
                    current.copy(
                        messages = current.messages + ChatMessage(
                            id = result.value.messageId,
                            text = result.value.reply,
                            author = ChatMessage.Author.AURA,
                        ),
                        isSending = false,
                        connection = ConnectionState.Connected(
                            (current.connection as? ConnectionState.Connected)
                                ?.provider ?: "aura"
                        ),
                    )
                }
            }

            is AuraResult.Failed -> {
                slowNotice.cancel()
                _state.update { current ->
                    current.copy(
                        messages = current.messages.map { message ->
                            if (message.id == outgoingId) {
                                message.copy(failed = true)
                            } else {
                                message
                            }
                        },
                        isSending = false,
                        error = result.error,
                        connection = ConnectionState.Unavailable(
                            result.error.userMessage
                        ),
                    )
                }
            }
        }
    }

    /**
     * Retry a message that failed, without making the user retype it.
     */
    fun retry(messageId: String) {

        val failed = _state.value.messages.firstOrNull {
            it.id == messageId && it.failed
        } ?: return

        _state.update { current ->
            current.copy(
                messages = current.messages.filterNot { it.id == messageId },
                draft = failed.text,
            )
        }

        send()
    }

    /**
     * Show a companion message that arrived out of band.
     *
     * Called when the user opens the app from a notification, so the thing
     * they tapped is visible in the conversation rather than being a
     * notification that led to an empty screen.
     */
    fun showCompanionMessage(text: String) {

        if (text.isBlank()) return

        if (_state.value.messages.any {
                it.author == ChatMessage.Author.AURA && it.text == text
            }
        ) {
            return
        }

        _state.update { current ->
            current.copy(
                messages = current.messages + ChatMessage(
                    id = UUID.randomUUID().toString(),
                    text = text,
                    author = ChatMessage.Author.AURA,
                )
            )
        }
    }

    companion object {

        /** How long a request may take before we explain the wait. */
        private const val WAKE_NOTICE_MS = 4_000L

        fun factory(
            repository: AuraRepository,
            settings: SettingsProvider,
            transcript: Transcript = Transcript.None,
        ): ViewModelProvider.Factory = object : ViewModelProvider.Factory {

            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T =
                ChatViewModel(repository, settings, transcript) as T
        }
    }
}
