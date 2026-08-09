package com.aura.companion.ui.chat

import com.aura.companion.data.AuraError

/**
 * Everything the chat screen renders, in one immutable object.
 *
 * The screen is a pure function of this. There is no state hidden in a
 * Composable, which is what makes "the send button spun forever" a bug
 * you can reproduce from a data class rather than from a sequence of
 * taps.
 */
data class ChatUiState(
    val messages: List<ChatMessage> = emptyList(),
    val draft: String = "",
    val connection: ConnectionState = ConnectionState.Unknown,
    val isSending: Boolean = false,
    val error: AuraError? = null,
    val isConfigured: Boolean = false,
) {
    val canSend: Boolean
        get() = draft.isNotBlank() && !isSending && isConfigured
}

data class ChatMessage(
    val id: String,
    val text: String,
    val author: Author,
    val timestamp: Long = System.currentTimeMillis(),
    val failed: Boolean = false,
    /**
     * True while this reply is still arriving.
     *
     * Kept on the message rather than in [ChatUiState] because the screen
     * needs to know *which* bubble is growing - and once streaming exists,
     * "Aura is typing" and "this specific reply is incomplete" are no
     * longer the same fact.
     */
    val streaming: Boolean = false,
) {
    enum class Author { USER, AURA }
}

/**
 * What the connection banner says.
 *
 * `WakingUp` is its own state rather than a flavour of Connecting: on a
 * free tier the first request after an idle period takes tens of seconds,
 * and a user watching a spinner with no explanation assumes it is broken
 * and force-quits at about fifteen.
 */
sealed interface ConnectionState {
    data object Unknown : ConnectionState
    data object Connecting : ConnectionState
    data object WakingUp : ConnectionState
    data class Connected(val provider: String) : ConnectionState
    data class Unavailable(val reason: String) : ConnectionState
}
