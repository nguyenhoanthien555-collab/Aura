package com.aura.companion.sync

import com.aura.companion.data.remote.ApiFactory
import com.aura.companion.data.remote.SyncEventDto
import com.aura.companion.data.settings.FakeSettings
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files

class SyncBreakTest {

    private lateinit var server: MockWebServer
    private lateinit var tempDir: File
    private lateinit var settings: FakeSettings
    private lateinit var outbox: FileEventOutbox
    private lateinit var inbox: FileEventInbox
    private lateinit var cursorStore: FileCursorStore
    private lateinit var syncClient: SyncClient

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()

        tempDir = Files.createTempDirectory("aura_break_test").toFile()
        settings = FakeSettings(
            serverUrl = server.url("/").toString(),
            authToken = "test-token",
            deviceId = "test-break-device"
        )
        outbox = FileEventOutbox(File(tempDir, "outbox"))
        inbox = FileEventInbox(File(tempDir, "inbox"))
        cursorStore = FileCursorStore(File(tempDir, "cursor"))

        val apiSupplier = {
            ApiFactory.create(settings, settings.current.serverUrl)
        }

        syncClient = SyncClient(
            api = apiSupplier,
            settings = settings,
            outbox = outbox,
            inbox = inbox,
            cursorStore = cursorStore
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
        tempDir.deleteRecursively()
    }

    // ----------------------------------------------------------------------
    // ATTACK 1: Cursor Regression Vulnerability
    // ----------------------------------------------------------------------
    @Test
    fun attackCursorRegression() {
        cursorStore.setCursor("RENDER", 100L)
        assertEquals(100L, cursorStore.getCursor("RENDER"))

        // Regressive sequence must NOT overwrite a higher sequence!
        cursorStore.setCursor("RENDER", 99L)
        assertEquals("Cursor must reject regression and remain at 100", 100L, cursorStore.getCursor("RENDER"))
    }

    // ----------------------------------------------------------------------
    // ATTACK 2: Duplicate-Only Pull Cursor Advance Vulnerability
    // ----------------------------------------------------------------------
    @Test
    fun attackDuplicateOnlyPullCursorAdvance() = runTest {
        val payload = buildJsonObject { put("cmd", "test") }
        val payloadHash = CanonicalJson.computeHash(payload)

        val evt = SyncEventDto(
            eventId = "evt-dup-1",
            originNodeId = "RENDER",
            logicalSequence = 50L,
            eventType = "DEVICE_ACTION",
            payload = payload,
            payloadHash = payloadHash,
            createdAt = "2026-09-17T00:00:00Z"
        )

        // Pre-record in inbox as already applied (e.g. from prior pull before ACK lost)
        inbox.recordApplied(evt)

        // Server re-delivers this duplicate event with logical sequence 50
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"status":"ok","events":[{"event_id":"evt-dup-1","origin_node_id":"RENDER","logical_sequence":50,"event_type":"DEVICE_ACTION","payload":{"cmd":"test"},"payload_hash":"$payloadHash","created_at":"2026-09-17T00:00:00Z"}],"cursor":50,"has_more":false}"""
                )
        )
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"status":"ok","acknowledged":["evt-dup-1"]}""")
        )

        assertEquals(0L, cursorStore.getCursor("RENDER"))
        val count = syncClient.pullIncoming()
        assertEquals(1, count)

        // VULNERABILITY: In current code, cursorStore.setCursor was NOT called on IdempotentDuplicate!
        // The cursor MUST advance to 50L so we don't re-pull it on the next cycle!
        assertEquals("Cursor must advance to 50 even when batch contains duplicate events", 50L, cursorStore.getCursor("RENDER"))
    }

    // ----------------------------------------------------------------------
    // ATTACK 3: Journal Corruption Recovery Vulnerability
    // ----------------------------------------------------------------------
    @Test
    fun attackJournalCorruptionRecovery() {
        val journalDir = File(tempDir, "corrupt_outbox")
        journalDir.mkdirs()
        val journalFile = File(journalDir, "outbox_journal.jsonl")

        val payload = buildJsonObject { put("a", 1) }
        val hash = CanonicalJson.computeHash(payload)

        val line1 = """{"event":{"event_id":"evt-valid-1","origin_node_id":"test","event_type":"ACTION","entity_type":"","entity_id":"","schema_version":1,"created_at":"2026-09-17T00:00:00Z","logical_sequence":1,"payload":{"a":1},"payload_hash":"$hash"},"status":"PENDING"}"""
        val corruptLine = """{"event":{"event_id":"evt-corrupt-2", "partial_json"""
        val line3 = """{"event":{"event_id":"evt-valid-3","origin_node_id":"test","event_type":"ACTION","entity_type":"","entity_id":"","schema_version":1,"created_at":"2026-09-17T00:00:00Z","logical_sequence":3,"payload":{"a":1},"payload_hash":"$hash"},"status":"PENDING"}"""

        journalFile.writeText("$line1\n$corruptLine\n$line3\n")

        val reloadedOutbox = FileEventOutbox(journalDir)
        val pending = reloadedOutbox.getPending(10)

        // In current code: Line 3 is lost because forEachLine aborted on Line 2!
        // Correct behavior: Valid line 1 and valid line 3 MUST both be recovered!
        val ids = pending.map { it.eventId }
        assertTrue("Valid line 1 must be recovered", ids.contains("evt-valid-1"))
        assertTrue("Valid line 3 must be recovered despite corrupted line 2", ids.contains("evt-valid-3"))
        assertEquals("Expected exactly 2 valid recovered events", 2, pending.size)
    }
}
