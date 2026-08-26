package com.aura.companion.data.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * §15: what survives the app closing, and what deliberately does not.
 *
 * The requirement is one sentence - "AURA's chat UI must NOT lose visible
 * history when the application closes" - and the whole of it rests on this
 * file round-tripping. [TranscriptStore] cannot be tested here: it needs a
 * `Context` and a Keystore-backed key, so a JVM test dies in its constructor
 * before reaching anything worth asserting on, the same trade-off
 * `SettingsStore` documents. So the store is a thin shell over these two
 * pure functions, and the parts with bugs in them - the bound, a corrupt
 * file, an author nobody recognises - are all here.
 *
 * The stored shape is deliberately not [com.aura.companion.ui.chat.ChatMessage].
 * A `data` type may not depend on a `ui` one, and there is a second benefit
 * that matters more: `streaming` has no column, so a reply that was still
 * arriving when the process died cannot come back as a bubble that spins
 * forever. That is a guarantee from the format rather than from a caller
 * remembering to clear a flag.
 */
class TranscriptCodecTest {

    // ------------------------------------------------------------------
    // Round trip
    // ------------------------------------------------------------------

    @Test
    fun `a conversation comes back the way it went in`() {

        val saved = StoredConversation(
            messages = listOf(
                message("1", "cậu ơi giúp tớ với", Author.USER),
                message("2", "được, cậu gửi log đi", Author.AURA),
            ),
            sessionId = "session-7",
        )

        assertEquals(saved, TranscriptCodec.decode(TranscriptCodec.encode(saved)))
    }

    @Test
    fun `Vietnamese, emoji and code survive intact`() {

        // The transcript is Vietnamese by default and holds whatever Aura
        // wrote, snippets included. A codec that mangled diacritics would
        // lose the register the persona layer exists to protect.
        val text = "xong rồi 💀 chạy `pytest -q` là ra ngay ơi"

        val saved = StoredConversation(listOf(message("1", text, Author.AURA)))

        assertEquals(text, TranscriptCodec.decode(TranscriptCodec.encode(saved)).messages.single().text)
    }

    @Test
    fun `every field the screen renders from survives`() {

        // The id keys the bubble in the list, and the timestamp orders it.
        // Losing either shows up as a list that reshuffles on restart.
        val one = StoredMessage(id = "abc-123", text = "hi", author = Author.USER, timestamp = 1_700_000_000_000L)

        val back = TranscriptCodec.decode(
            TranscriptCodec.encode(StoredConversation(listOf(one)))
        ).messages.single()

        assertEquals("abc-123", back.id)
        assertEquals(1_700_000_000_000L, back.timestamp)
        assertEquals(Author.USER, back.author)
    }

    @Test
    fun `a message that failed to send keeps its mark`() {

        // `retry()` finds the failed message by this flag. Dropping it would
        // restore a message the user can see and cannot resend.
        val saved = StoredConversation(
            listOf(StoredMessage("1", "sent nothing", Author.USER, 1L, failed = true))
        )

        assertTrue(TranscriptCodec.decode(TranscriptCodec.encode(saved)).messages.single().failed)
    }

    @Test
    fun `no session id is an absence rather than an empty string`() {

        // "" is a session the server would be asked to continue. null is the
        // request that allocates a new one, and the two are not the same ask.
        val back = TranscriptCodec.decode(
            TranscriptCodec.encode(StoredConversation(listOf(message("1", "hi", Author.USER))))
        )

        assertNull(back.sessionId)
    }

    // ------------------------------------------------------------------
    // What the format refuses to carry
    // ------------------------------------------------------------------

    @Test
    fun `a reply that was still arriving cannot come back spinning`() {

        // `streaming` has no field to be stored in, so this is not a matter
        // of a loader remembering to clear it. Asserted through the encoded
        // text because that is where the guarantee actually lives: if
        // somebody adds the field later, this fails.
        val encoded = TranscriptCodec.encode(
            StoredConversation(listOf(message("1", "đang gõ...", Author.AURA)))
        )

        assertTrue("streaming must have nowhere to be stored", !encoded.contains("streaming"))
    }

    // ------------------------------------------------------------------
    // A file that is not what we hoped
    // ------------------------------------------------------------------

    @Test
    fun `a corrupt file reads as no history rather than a crash`() {

        // This runs during `init`, before the chat screen has drawn. An
        // exception here is not a lost transcript, it is an app that cannot
        // open - so a half-written file has to degrade to the state the app
        // was in before any of this existed.
        for (junk in listOf("", "   ", "{", "not json at all", "[1,2,3]", "{\"messages\":\"nope\"}")) {
            val back = TranscriptCodec.decode(junk)

            assertTrue("`$junk` should decode to nothing", back.messages.isEmpty())
            assertNull(back.sessionId)
        }
    }

    @Test
    fun `one unreadable message does not take the rest with it`() {

        // Losing the whole conversation to a single bad row is the same
        // failure as a crash, just quieter. Hand-written JSON rather than
        // encoded, because the point is a file this codec would not produce.
        val text = """
            {"messages":[
              {"id":"1","text":"still here","author":"USER","timestamp":1},
              {"id":"2","author":"AURA","timestamp":2},
              {"id":"3","text":"and here","author":"USER","timestamp":3}
            ]}
        """.trimIndent()

        val back = TranscriptCodec.decode(text)

        assertEquals(listOf("still here", "and here"), back.messages.map { it.text })
    }

    @Test
    fun `an author nobody recognises is dropped rather than guessed`() {

        // Guessing would put words in somebody's mouth: rendering an unknown
        // author as AURA shows the user something Aura never said, and as
        // USER shows them something they never said. Neither is recoverable
        // by looking at it. Same bargain `read_action_history` makes.
        val text = """
            {"messages":[
              {"id":"1","text":"mine","author":"USER","timestamp":1},
              {"id":"2","text":"whose?","author":"SYSTEM","timestamp":2}
            ]}
        """.trimIndent()

        val back = TranscriptCodec.decode(text)

        assertEquals(listOf("mine"), back.messages.map { it.text })
    }

    @Test
    fun `a field added by a later version is ignored, not fatal`() {

        // An app that is downgraded, or a format that grows a column, must
        // not lose the user's history over a field this build never asked
        // for.
        val text = """{"messages":[{"id":"1","text":"hi","author":"USER","timestamp":1,"mood":"warm"}]}"""

        assertEquals("hi", TranscriptCodec.decode(text).messages.single().text)
    }

    // ------------------------------------------------------------------
    // The bound
    // ------------------------------------------------------------------

    @Test
    fun `a long conversation keeps its newest messages`() {

        // EncryptedSharedPreferences rewrites the whole value on every
        // commit, so an unbounded transcript makes each save more expensive
        // than the last until the chat screen stutters. The bound is a real
        // loss and is recorded as one: past this many messages, the oldest
        // stop surviving a restart.
        //
        // Newest rather than oldest because the screen opens at the bottom
        // and the recent turns are the ones the next reply follows from.
        val many = (1..TranscriptCodec.MAX_MESSAGES + 50).map {
            message(it.toString(), "message $it", Author.USER)
        }

        val back = TranscriptCodec.decode(TranscriptCodec.encode(StoredConversation(many)))

        assertEquals(TranscriptCodec.MAX_MESSAGES, back.messages.size)
        assertEquals("message ${many.size}", back.messages.last().text)
        assertEquals("message 51", back.messages.first().text)
    }

    @Test
    fun `the bound is applied by the codec, not asked of the caller`() {

        // If trimming were the store's job, the one place that forgot would
        // grow without limit and nothing would notice until it was slow.
        val many = (1..TranscriptCodec.MAX_MESSAGES + 1).map {
            message(it.toString(), "m$it", Author.AURA)
        }

        assertEquals(TranscriptCodec.MAX_MESSAGES, TranscriptCodec.bound(many).size)
        assertEquals(3, TranscriptCodec.bound(many.take(3)).size)
    }

    @Test
    fun `trimming the transcript does not drop the session id`() {

        // The id is what makes a restored transcript the same conversation
        // the server remembers. Losing it during a trim would show the user
        // their own history beside an Aura who had never seen it.
        val many = (1..TranscriptCodec.MAX_MESSAGES + 5).map {
            message(it.toString(), "m$it", Author.USER)
        }

        val back = TranscriptCodec.decode(
            TranscriptCodec.encode(StoredConversation(many, sessionId = "keep-me"))
        )

        assertEquals("keep-me", back.sessionId)
    }

    @Test
    fun `an empty conversation round trips as empty`() {

        // Reached by `newConversation()`, which has to be able to say "there
        // is nothing" and have it mean that on the next launch.
        val back = TranscriptCodec.decode(TranscriptCodec.encode(StoredConversation()))

        assertTrue(back.messages.isEmpty())
        assertNull(back.sessionId)
    }

    // ------------------------------------------------------------------

    private fun message(id: String, text: String, author: Author) =
        StoredMessage(id = id, text = text, author = author, timestamp = id.hashCode().toLong())
}
