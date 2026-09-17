package com.aura.companion.sync

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import kotlinx.serialization.json.putJsonObject
import org.junit.Assert.assertEquals
import org.junit.Test

class CanonicalJsonTest {

    @Test
    fun testCanonicalOrderPrimitives() {
        val obj = buildJsonObject {
            put("b", 2)
            put("a", 1)
        }
        val canonical = CanonicalJson.canonicalize(obj)
        assertEquals("{\"a\":1,\"b\":2}", canonical)
    }

    @Test
    fun testNestedCanonicalSerialization() {
        val obj = buildJsonObject {
            put("b", 2)
            put("a", 1)
            putJsonObject("nested") {
                putJsonArray("y") {
                    add(kotlinx.serialization.json.JsonPrimitive(3))
                    add(kotlinx.serialization.json.JsonPrimitive(2))
                    add(kotlinx.serialization.json.JsonPrimitive(1))
                }
                put("x", "hello")
            }
        }
        val canonical = CanonicalJson.canonicalize(obj)
        assertEquals("{\"a\":1,\"b\":2,\"nested\":{\"x\":\"hello\",\"y\":[3,2,1]}}", canonical)

        val hash = CanonicalJson.computeHash(obj)
        // Verified with Python hashlib.sha256:
        assertEquals("5ce1b3e87330e9d0cf8d5da65008aa26de28c0e75d645eb252f21f327c00bae3", hash)
    }

    @Test
    fun testPayloadHashParityWithPython() {
        val obj = buildJsonObject {
            put("action", "app_open")
            put("package", "com.android.settings")
        }
        val canonical = CanonicalJson.canonicalize(obj)
        assertEquals("{\"action\":\"app_open\",\"package\":\"com.android.settings\"}", canonical)

        val hash = CanonicalJson.computeHash(obj)
        // Bit-for-bit parity with Python compute_payload_hash()
        assertEquals("19658421d81bc7cd3f3db907844edff936b2f045020789b554fe81b0b2f75410", hash)
    }

    @Test
    fun testEscapingAndUnicode() {
        val obj = buildJsonObject {
            put("quote", "hello \"world\"")
            put("newline", "line1\nline2")
        }
        val canonical = CanonicalJson.canonicalize(obj)
        assertEquals("{\"newline\":\"line1\\nline2\",\"quote\":\"hello \\\"world\\\"\"}", canonical)
    }
}
