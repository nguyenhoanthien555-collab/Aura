package com.aura.companion.data.chat

/**
 * What survives the app closing.
 *
 * §15 is one sentence - "AURA's chat UI must NOT lose visible history when
 * the application closes" - and until this existed the chat screen started
 * empty every time, because [com.aura.companion.ui.chat.ChatViewModel] built
 * its state from nothing and the session id lived in an `AtomicReference`
 * that died with the process.
 *
 * TWO INTERFACES, ONE STORE
 * -------------------------
 * [Transcript] is what the chat ViewModel needs: the bubbles. [SessionStore]
 * is what the repository needs: the id that makes those bubbles a
 * conversation the server also remembers. They are separate because their
 * owners are separate - the repository has always owned the session id, and
 * handing it a transcript it has no business reading would invite a second
 * writer of the visible history.
 *
 * Restoring one without the other is worse than restoring neither. Bubbles
 * with a fresh session id show the user their own conversation beside an Aura
 * who has never seen it, and §38 names that failure directly: the user should
 * never have to explain the situation again merely because something
 * restarted. So one implementation serves both, and writes them together.
 *
 * WHY THE STORED SHAPE IS NOT `ChatMessage`
 * -----------------------------------------
 * `ChatMessage` lives in `ui.chat`, and `data` may not depend on `ui`. The
 * layering rule pays for itself here: [StoredMessage] has no `streaming`
 * field, so a reply that was still arriving when the process died cannot come
 * back as a bubble that spins forever with no request behind it. That is a
 * guarantee from the format rather than from a loader remembering to clear a
 * flag.
 *
 * `failed` *is* stored, because `retry()` finds a message by it. A restored
 * message the user can see and cannot resend would be worse than one that was
 * never restored.
 *
 * Implementations may throw. Nothing here is worth an app that will not open,
 * so every caller is defensive - and a failed read must never be mistaken for
 * an empty conversation, because writing that emptiness back would destroy
 * the transcript the read failed to produce (§41).
 */

/** Who wrote a stored message. Deliberately not `ChatMessage.Author`. */
enum class Author { USER, AURA }

data class StoredMessage(
    val id: String,
    val text: String,
    val author: Author,
    val timestamp: Long,
    val failed: Boolean = false,
)

data class StoredConversation(
    val messages: List<StoredMessage> = emptyList(),
    val sessionId: String? = null,
)

/** The visible history. Owned by the chat ViewModel. */
interface Transcript {

    fun read(): StoredConversation

    /** Replace the stored messages, leaving the session id alone. */
    fun write(messages: List<StoredMessage>)

    /** Forget the messages. Only ever called on purpose. */
    fun clear()

    /**
     * A store that keeps nothing.
     *
     * The default for [com.aura.companion.data.AuraRepository] and the
     * ViewModel, so every existing caller and test compiles and behaves
     * exactly as it did before persistence existed - an empty read, and a
     * write that goes nowhere.
     */
    object None : Transcript {
        override fun read() = StoredConversation()
        override fun write(messages: List<StoredMessage>) = Unit
        override fun clear() = Unit
    }
}

/** The session id alone. Owned by the repository. */
interface SessionStore {

    val sessionId: String?

    /** Store this session id, or forget it when null. */
    fun remember(sessionId: String?)

    object None : SessionStore {
        override val sessionId: String? = null
        override fun remember(sessionId: String?) = Unit
    }
}
