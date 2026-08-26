package com.aura.companion.data.chat

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * The conversation on disk.
 *
 * Encrypted, in its own file, under the same Keystore-backed master key
 * `SettingsStore` uses. The transcript is not a credential, but it is
 * everything the owner has said to Aura, which makes it the most personal
 * thing this app stores - and encrypting it costs one line here, because the
 * key already exists.
 *
 * Its own file rather than a few more keys in the settings file, for a
 * mundane reason that would otherwise bite: EncryptedSharedPreferences
 * rewrites the whole file on commit, and a transcript saved every turn would
 * make the server URL and the auth token share that churn.
 *
 * ONE OBJECT, TWO INTERFACES
 * --------------------------
 * [Transcript] for the chat ViewModel, [SessionStore] for the repository. The
 * in-memory [held] mirror is what lets each of them write its own half
 * without reading the other's: `write` keeps the session id, `remember` keeps
 * the messages, and neither has to parse the file to do it.
 *
 * NOTHING HERE THROWS
 * -------------------
 * A Keystore can become unavailable - a locked device, an app restored from a
 * backup onto different hardware - and an exception on this path is an app
 * that will not open or a message that will not send. Every failure is logged
 * without its contents and swallowed. The interface still declares that
 * implementations may throw, and callers are still defensive: what is caught
 * here is what this class can foresee, and a caller that trusted it would be
 * one surprise away from losing the transcript it failed to read.
 */
class TranscriptStore(context: Context) : Transcript, SessionStore {

    private val prefs: SharedPreferences? = create(context)

    /**
     * The last thing successfully read or written.
     *
     * Not a cache for speed - a cache for coherence. The two halves of this
     * file are written by two different owners, and this is what stops one
     * from erasing the other's.
     */
    private var held: StoredConversation = load()

    override val sessionId: String? get() = held.sessionId

    override fun read(): StoredConversation = held

    override fun write(messages: List<StoredMessage>) {
        save(held.copy(messages = TranscriptCodec.bound(messages)))
    }

    override fun remember(sessionId: String?) {
        save(held.copy(sessionId = sessionId?.takeIf { it.isNotBlank() }))
    }

    override fun clear() {
        // The messages only. Forgetting the session is `remember(null)`, and
        // `newConversation` does both - through the two owners that each know
        // about one of them.
        save(held.copy(messages = emptyList()))
    }

    private fun save(conversation: StoredConversation) {

        // Held first, so an unwritable file still gives a coherent process.
        // Losing the transcript at the next launch is bad; losing the session
        // id mid-conversation, while the user is still typing into it, is
        // worse.
        held = conversation

        val prefs = prefs ?: return

        try {
            prefs.edit().putString(KEY, TranscriptCodec.encode(conversation)).apply()
        } catch (error: Exception) {
            Log.w(TAG, "could not save the transcript: ${error.javaClass.simpleName}")
        }
    }

    private fun load(): StoredConversation {

        val stored = try {
            prefs?.getString(KEY, null)
        } catch (error: Exception) {
            Log.w(TAG, "could not read the transcript: ${error.javaClass.simpleName}")
            null
        }

        return stored?.let { TranscriptCodec.decode(it) } ?: StoredConversation()
    }

    /**
     * The encrypted file, or null if this device cannot give us one.
     *
     * Null rather than an exception: a phone whose Keystore has been
     * invalidated must still run the app, just without remembering the
     * conversation. Every method above already handles the absence, because
     * an unwritable file and a missing one need the same behaviour.
     */
    private fun create(context: Context): SharedPreferences? = try {

        val key = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()

        EncryptedSharedPreferences.create(
            context,
            FILE,
            key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    } catch (error: Exception) {
        Log.w(TAG, "no encrypted store for the transcript: ${error.javaClass.simpleName}")
        null
    }

    private companion object {
        const val TAG = "TranscriptStore"
        const val FILE = "aura_transcript"
        const val KEY = "conversation"
    }
}
