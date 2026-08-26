package com.aura.companion.data.chat

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * The transcript, to text and back.
 *
 * Pure on purpose. [TranscriptStore] needs a `Context` and a Keystore-backed
 * key, so a JVM test dies in its constructor before reaching anything worth
 * asserting on - the same trade-off `SettingsStore` documents, and the reason
 * `SettingsProvider` exists at all. So the store is a thin shell and
 * everything that can be wrong lives here, where
 * `TranscriptCodecTest` can reach it: the bound, a half-written file, an
 * author nobody recognises.
 *
 * BUILT AS A TREE, NOT FROM `@Serializable`
 * -----------------------------------------
 * `decodeFromString<StoredConversation>` would be shorter and would throw on
 * the first row that is missing a field, taking the whole conversation with
 * it. Walking the tree is what makes per-row tolerance possible, and it also
 * makes the absent `streaming` column visible in one place rather than
 * implied by a data class three files away.
 *
 * Strict `Json` rather than the lenient instance in `ApiFactory`: this is a
 * file we wrote ourselves, not a wire format from someone else's server, so
 * anything unparseable is corruption rather than a dialect to accommodate -
 * and corruption is handled by degrading, below, not by guessing.
 */
object TranscriptCodec {

    /**
     * How many messages survive a restart.
     *
     * A real loss, and recorded as one: past this, the oldest messages stop
     * being restored. The number is not arbitrary - EncryptedSharedPreferences
     * rewrites the entire value on every commit, so an unbounded transcript
     * makes each save more expensive than the last, and a save happens on
     * every turn. Two hundred turns of scrollback is far more than the screen
     * is used for and cheap enough that the write never shows.
     */
    const val MAX_MESSAGES = 200

    private const val MESSAGES = "messages"
    private const val SESSION = "session_id"

    private const val ID = "id"
    private const val TEXT = "text"
    private const val AUTHOR = "author"
    private const val TIMESTAMP = "timestamp"
    private const val FAILED = "failed"

    /** The newest [MAX_MESSAGES], oldest dropped. */
    fun bound(messages: List<StoredMessage>): List<StoredMessage> =
        if (messages.size <= MAX_MESSAGES) messages
        else messages.subList(messages.size - MAX_MESSAGES, messages.size)

    /**
     * The conversation as text, trimmed to the bound.
     *
     * Trimming here rather than in the caller so there is no version of this
     * that forgets: a store that grew without limit would not fail, it would
     * only get slower, which is the kind of bug nobody reports.
     */
    fun encode(conversation: StoredConversation): String = buildJsonObject {

        conversation.sessionId?.let { put(SESSION, JsonPrimitive(it)) }

        put(MESSAGES, buildJsonArray {
            bound(conversation.messages).forEach { message ->
                add(buildJsonObject {
                    put(ID, JsonPrimitive(message.id))
                    put(TEXT, JsonPrimitive(message.text))
                    put(AUTHOR, JsonPrimitive(message.author.name))
                    put(TIMESTAMP, JsonPrimitive(message.timestamp))
                    if (message.failed) put(FAILED, JsonPrimitive(true))
                })
            }
        })
    }.toString()

    /**
     * Whatever of the conversation can be read.
     *
     * Never throws. This runs during `init`, before the chat screen has
     * drawn, so an exception is not a lost transcript - it is an app that
     * cannot open. Corruption degrades to the state the app was in before any
     * of this existed: an empty screen, which is recoverable by typing.
     *
     * A row missing a field is skipped rather than fatal, and an author this
     * build does not recognise is dropped rather than guessed. Guessing would
     * put words in somebody's mouth - rendering an unknown author as AURA
     * shows the user something Aura never said - and unlike a missing message,
     * that is not visibly wrong.
     */
    fun decode(text: String): StoredConversation {

        val root = runCatching { Json.parseToJsonElement(text).jsonObject }
            .getOrNull()
            ?: return StoredConversation()

        val rows = runCatching { root[MESSAGES]?.jsonArray }
            .getOrNull()
            ?: return StoredConversation()

        return StoredConversation(
            messages = rows.mapNotNull { row ->
                runCatching { message(row.jsonObject) }.getOrNull()
            },
            sessionId = runCatching { root[SESSION]?.jsonPrimitive?.content }
                .getOrNull()
                ?.takeIf { it.isNotBlank() },
        )
    }

    /** One row, or null if it is not one. Throwing is caught by the caller. */
    private fun message(row: JsonObject): StoredMessage? {

        val author = Author.entries.firstOrNull {
            it.name == row[AUTHOR]?.jsonPrimitive?.content
        } ?: return null

        return StoredMessage(
            id = row.getValue(ID).jsonPrimitive.content,
            text = row.getValue(TEXT).jsonPrimitive.content,
            author = author,
            timestamp = row.getValue(TIMESTAMP).jsonPrimitive.content.toLong(),
            failed = row[FAILED]?.jsonPrimitive?.content?.toBoolean() ?: false,
        )
    }
}
