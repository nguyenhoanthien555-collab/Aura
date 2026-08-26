package com.aura.companion.accessibility

import java.security.MessageDigest
import java.util.UUID

/**
 * Identity and integrity helpers for device observations (PARTS 6-8).
 *
 * Every observation the phone reports carries an id minted here and a
 * content hash computed here, because "is this the screen I just saw?"
 * must be decidable from the payload alone - the same property the
 * server's observation engine gives its own records.
 */
object ObservationIds {

    fun newObservationId(): String =
        "obs_" + UUID.randomUUID().toString().replace("-", "").take(16)

    /** Epoch seconds - the server stores observations as float seconds. */
    fun nowEpochSeconds(): Double = System.currentTimeMillis() / 1000.0

    /**
     * A stable sha-256 of any JSON-shaped content, hex-encoded without
     * prefix. Different content, different hash; identical content
     * proves nothing moved between two captures.
     */
    fun hashOf(vararg parts: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        parts.forEach { digest.update(it.toByteArray()) }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    /**
     * The same hash over raw bytes - for screenshots.
     *
     * Separate from [hashOf] because JPEG bytes are not text: putting
     * them through `String(bytes)` would replace every byte outside the
     * platform charset with the same replacement character, so two
     * different frames could hash identically. That is the one thing a
     * freshness hash must never do.
     */
    fun hashOfBytes(bytes: ByteArray): String {
        val digest = MessageDigest.getInstance("SHA-256")
        digest.update(bytes)
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}