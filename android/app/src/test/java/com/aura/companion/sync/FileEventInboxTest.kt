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

class FileEventInboxTest {

    private lateinit var tempDir: File
    private lateinit var inbox: FileEventInbox

    @Before
    fun setUp() {
        tempDir = Files.createTempDirectory("aura_inbox_test").toFile()
        inbox = FileEventInbox(tempDir)
    }

    @After
    fun tearDown() {
        tempDir.deleteRecursively()
    }

    private fun sampleEvent(id: String, action: String = "test"): SyncEventDto {
        val payload = buildJsonObject { put("action", action) }
        return SyncEventDto(
            eventId = id,
            originNodeId = "RENDER",
            logicalSequence = 1L,
            eventType = "DEVICE_INVOCATION",
            payload = payload,
            payloadHash = CanonicalJson.computeHash(payload),
            createdAt = "2026-09-17T00:00:00Z"
        )
    }

    @Test
    fun testFirstApplicationAndDuplicateSuppression() {
        val evt = sampleEvent("evt-100")

        // First application
        val res1 = inbox.shouldProcess(evt)
        assertTrue(res1 is InboxApplyResult.Applied)
        inbox.recordApplied(evt)

        // Exact same event arriving again -> idempotent duplicate
        val res2 = inbox.shouldProcess(evt)
        assertTrue(res2 is InboxApplyResult.IdempotentDuplicate)
    }

    @Test
    fun testPayloadHashMismatchRejection() {
        val valid = sampleEvent("evt-tampered")
        // Create tampered event with corrupted hash
        val tampered = valid.copy(payloadHash = "0000000000000000000000000000000000000000000000000000000000000000")

        val res = inbox.shouldProcess(tampered)
        assertTrue(res is InboxApplyResult.Conflict)
    }

    @Test
    fun testConflictingPayloadForSameId() {
        val evt1 = sampleEvent("evt-collision", "action1")
        inbox.recordApplied(evt1)

        val evt2 = sampleEvent("evt-collision", "action2")
        val res = inbox.shouldProcess(evt2)
        assertTrue(res is InboxApplyResult.Conflict)
    }

    @Test
    fun testDurabilityAcrossReload() {
        val evt = sampleEvent("evt-survives-restart")
        inbox.recordApplied(evt)

        // Simulate process crash / reload
        val reloadedInbox = FileEventInbox(tempDir)
        val res = reloadedInbox.shouldProcess(evt)
        assertTrue(res is InboxApplyResult.IdempotentDuplicate)
    }
}
