package com.aura.companion.sync

import com.aura.companion.data.remote.SyncEventDto
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files

class FileEventOutboxTest {

    private lateinit var tempDir: File
    private lateinit var outbox: FileEventOutbox

    @Before
    fun setUp() {
        tempDir = Files.createTempDirectory("aura_outbox_test").toFile()
        outbox = FileEventOutbox(tempDir)
    }

    @After
    fun tearDown() {
        tempDir.deleteRecursively()
    }

    private fun sampleEvent(id: String): SyncEventDto {
        val payload = buildJsonObject { put("action", "test") }
        return SyncEventDto(
            eventId = id,
            originNodeId = "android-test",
            logicalSequence = 1L,
            eventType = "DEVICE_ACTION",
            payload = payload,
            payloadHash = CanonicalJson.computeHash(payload),
            createdAt = "2026-09-17T00:00:00Z"
        )
    }

    @Test
    fun testEnqueueAndPending() {
        val evt1 = sampleEvent("evt-1")
        val evt2 = sampleEvent("evt-2")

        outbox.enqueue(evt1)
        outbox.enqueue(evt2)

        assertEquals(2, outbox.pendingCount())
        val pending = outbox.getPending(10)
        assertEquals(2, pending.size)
        assertEquals("evt-1", pending[0].eventId)
        assertEquals("evt-2", pending[1].eventId)
    }

    @Test
    fun testLifecycleTransitions() {
        val evt1 = sampleEvent("evt-1")
        outbox.enqueue(evt1)

        outbox.markSending(listOf("evt-1"))
        assertEquals(1, outbox.pendingCount())

        outbox.markRetry(listOf("evt-1"), "HTTP 500")
        assertEquals(1, outbox.pendingCount())

        outbox.markAcknowledged(listOf("evt-1"))
        assertEquals(0, outbox.pendingCount())
        assertEquals(0, outbox.getPending(10).size)
    }

    @Test
    fun testDurabilityAcrossReload() {
        val evt1 = sampleEvent("evt-1")
        val evt2 = sampleEvent("evt-2")
        outbox.enqueue(evt1)
        outbox.enqueue(evt2)
        outbox.markAcknowledged(listOf("evt-1"))

        // Simulate complete app / process restart
        val reloadedOutbox = FileEventOutbox(tempDir)
        assertEquals(1, reloadedOutbox.pendingCount())
        val pending = reloadedOutbox.getPending(10)
        assertEquals(1, pending.size)
        assertEquals("evt-2", pending[0].eventId)
    }

    @Test
    fun testQuarantine() {
        val evt1 = sampleEvent("evt-bad")
        outbox.enqueue(evt1)
        assertEquals(1, outbox.pendingCount())

        outbox.markQuarantined("evt-bad", "Schema conflict")
        assertEquals(0, outbox.pendingCount())
    }
}
