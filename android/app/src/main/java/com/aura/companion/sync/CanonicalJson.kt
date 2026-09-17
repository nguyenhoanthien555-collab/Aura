package com.aura.companion.sync

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.security.MessageDigest

/**
 * Deterministic JSON Canonicalization and Cryptographic Hashing for AURA.
 * Exactly matches Python's:
 *   json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
 *   hashlib.sha256(canonical.encode('utf-8')).hexdigest()
 */
object CanonicalJson {

    /**
     * Serializes a JsonElement deterministically:
     * - Object keys are sorted alphabetically.
     * - No whitespace around ':' or ','.
     * - Valid UTF-8, characters not escaped as ASCII unicode.
     */
    fun canonicalize(element: JsonElement): String {
        val sb = StringBuilder()
        serialize(element, sb)
        return sb.toString()
    }

    private fun serialize(element: JsonElement, sb: StringBuilder) {
        when (element) {
            is JsonObject -> {
                sb.append('{')
                val sortedKeys = element.keys.sorted()
                var first = true
                for (key in sortedKeys) {
                    if (!first) sb.append(',')
                    first = false
                    appendString(key, sb)
                    sb.append(':')
                    serialize(element.getValue(key), sb)
                }
                sb.append('}')
            }
            is JsonArray -> {
                sb.append('[')
                var first = true
                for (item in element) {
                    if (!first) sb.append(',')
                    first = false
                    serialize(item, sb)
                }
                sb.append(']')
            }
            is JsonPrimitive -> {
                if (element.isString) {
                    appendString(element.content, sb)
                } else if (element is JsonNull) {
                    sb.append("null")
                } else {
                    sb.append(element.content)
                }
            }
        }
    }

    private fun appendString(s: String, sb: StringBuilder) {
        sb.append('"')
        for (ch in s) {
            when (ch) {
                '"' -> sb.append("\\\"")
                '\\' -> sb.append("\\\\")
                '\b' -> sb.append("\\b")
                '\u000C' -> sb.append("\\f")
                '\n' -> sb.append("\\n")
                '\r' -> sb.append("\\r")
                '\t' -> sb.append("\\t")
                else -> {
                    if (ch.code < 0x20) {
                        sb.append(String.format("\\u%04x", ch.code))
                    } else {
                        sb.append(ch)
                    }
                }
            }
        }
        sb.append('"')
    }

    /**
     * Computes the SHA-256 hex digest of the canonical JSON string.
     */
    fun computeHash(payload: JsonElement): String {
        val canonical = canonicalize(payload)
        val digest = MessageDigest.getInstance("SHA-256")
        val bytes = digest.digest(canonical.toByteArray(Charsets.UTF_8))
        return bytes.joinToString("") { "%02x".format(it) }
    }
}
