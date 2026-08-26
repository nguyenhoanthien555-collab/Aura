package com.aura.companion.ui.chat

import com.aura.companion.data.chat.StoredMessage
import com.aura.companion.data.chat.Author as StoredAuthor

/**
 * Between what the screen renders and what the disk holds.
 *
 * Two small functions in their own file because they are the seam where the
 * layering rule is paid for: `data` may not import a `ui` type, so the store
 * has its own row shape and something above has to map. Putting that here
 * rather than inside [ChatViewModel] keeps the ViewModel about the
 * conversation and makes the one asymmetry easy to find.
 *
 * The asymmetry is `streaming`, and it only goes one way. There is nowhere to
 * store it, and that is deliberate: a reply that was still arriving when the
 * process died would otherwise come back as a bubble that spins forever with
 * no request behind it. Restoring it as `false` is not a compromise here - it
 * is the only value the type can produce.
 */

internal fun StoredMessage.rendered(): ChatMessage = ChatMessage(
    id = id,
    text = text,
    author = when (author) {
        StoredAuthor.USER -> ChatMessage.Author.USER
        StoredAuthor.AURA -> ChatMessage.Author.AURA
    },
    timestamp = timestamp,
    failed = failed,
)

internal fun ChatMessage.stored(): StoredMessage = StoredMessage(
    id = id,
    text = text,
    author = when (author) {
        ChatMessage.Author.USER -> StoredAuthor.USER
        ChatMessage.Author.AURA -> StoredAuthor.AURA
    },
    timestamp = timestamp,
    failed = failed,
)
