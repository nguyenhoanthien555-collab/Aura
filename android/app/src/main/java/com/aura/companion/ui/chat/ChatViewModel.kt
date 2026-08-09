package com.aura.companion.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.aura.companion.data.AuraError
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.AuraResult
import com.aura.companion.data.remote.StreamEvent
import com.aura.companion.data.settings.SettingsProvider
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
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
) : ViewModel() {

    private val _state = MutableStateFlow(
        ChatUiState(isConfigured = settings.current.isConfigured)
    )
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    private var probe: Job? = null

    init {
        viewModelScope.launch {
            settings.settings.collect { current ->
                _state.update { it.copy(isConfigured = current.isConfigured) }
            }
        }
        checkConnection()
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
     * carry the old context forward. The messages list is cleared too, but
     * that is cosmetic - the session id is what actually matters.
     */
    fun newConversation() {
        repository.resetSession()
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
                            // needs the user to go and fix something.
                            error = if (result.error is AuraError.Unauthorized) {
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

        if (com.aura.companion.accessibility.AuraAccessibilityService.isEnabled()) {
            _state.update {
                it.copy(
                    messages = it.messages + outgoing,
                    draft = "",
                    isSending = true,
                    error = null,
                )
            }
            val success = com.aura.companion.accessibility.AuraAccessibilityService.startAgentTask(text) { finalReply ->
                _state.update { current ->
                    current.copy(
                        messages = current.messages + ChatMessage(
                            id = UUID.randomUUID().toString(),
                            text = finalReply,
                            author = ChatMessage.Author.AURA,
                        ),
                        isSending = false
                    )
                }
            }
            if (success) return
        }

        _state.update {
            it.copy(
                messages = it.messages + outgoing,
                draft = "",
                isSending = true,
                error = null,
            )
        }

        viewModelScope.launch {

            val slowNotice = launch {
                delay(WAKE_NOTICE_MS)
                if (isActive) {
                    _state.update { it.copy(connection = ConnectionState.WakingUp) }
                }
            }

            val streamed = streamReply(text, slowNotice)

            // Falling back rather than reporting a failure: a proxy that
            // will not carry a WebSocket is a deployment property, not
            // something the person typing can fix. REST answers the same
            // question with the same session, so the conversation
            // continues and the only visible difference is that the reply
            // arrives whole instead of growing.
            if (!streamed) {
                sendOverRest(outgoing.id, text, slowNotice)
            }
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
    private suspend fun streamReply(message: String, slowNotice: Job): Boolean {

        val messageId = UUID.randomUUID().toString()

        var reply = ""

        var failure: AuraError? = null

        repository.stream(message).collect { event ->

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
    ) {

        when (val result = repository.send(text)) {

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
        ): ViewModelProvider.Factory = object : ViewModelProvider.Factory {

            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T =
                ChatViewModel(repository, settings) as T
        }
    }
}
