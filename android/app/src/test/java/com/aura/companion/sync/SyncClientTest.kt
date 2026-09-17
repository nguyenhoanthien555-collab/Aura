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

class SyncClientTest {

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

        tempDir = Files.createTempDirectory("aura_sync_client_test").toFile()
        settings = FakeSettings(
            serverUrl = server.url("/").toString(),
            authToken = "test-token",
            deviceId = "test-device-uuid"
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

    @Test
    fun testRegisterNodeSuccess() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"status":"ok","node_id":"test-device-uuid","last_seen":"2026-09-17T00:00:00Z"}""")
        )

        val success = syncClient.registerNode()
        assertTrue(success)

        val req = server.takeRequest()
        assertEquals("/api/sync/register", req.path)
        assertTrue(req.body.readUtf8().contains("test-device-uuid"))
    }

    @Test
    fun testPushPendingEventsSuccess() = runTest {
        val payload = buildJsonObject { put("key", "value") }
        val evt = SyncEventDto(
            eventId = "evt-push-1",
            originNodeId = "test-device-uuid",
            logicalSequence = 1L,
            eventType = "SCREEN_STATE",
            payload = payload,
            payloadHash = CanonicalJson.computeHash(payload),
            createdAt = "2026-09-17T00:00:00Z"
        )
        outbox.enqueue(evt)
        assertEquals(1, outbox.pendingCount())

        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"status":"ok","acknowledged":["evt-push-1"],"conflicts":[]}""")
        )

        val pushed = syncClient.pushPending()
        assertEquals(1, pushed)
        assertEquals(0, outbox.pendingCount())

        val req = server.takeRequest()
        assertEquals("/api/sync/events/push", req.path)
    }

    @Test
    fun testPushPreservesEventsOnFailure() = runTest {
        val payload = buildJsonObject { put("key", "value") }
        val evt = SyncEventDto(
            eventId = "evt-fail-1",
            originNodeId = "test-device-uuid",
            logicalSequence = 1L,
            eventType = "SCREEN_STATE",
            payload = payload,
            payloadHash = CanonicalJson.computeHash(payload),
            createdAt = "2026-09-17T00:00:00Z"
        )
        outbox.enqueue(evt)
        assertEquals(1, outbox.pendingCount())

        server.enqueue(
            MockResponse()
                .setResponseCode(500)
                .setBody("""{"detail":"Internal Server Error"}""")
        )

        val pushed = syncClient.pushPending()
        assertEquals(0, pushed)
        // INVARIANT: event MUST NOT be dropped on HTTP failure!
        assertEquals(1, outbox.pendingCount())
    }

    @Test
    fun testPullIncomingAppliesAndAdvancesCursor() = runTest {
        val payload = buildJsonObject { put("command", "vibrate") }
        val payloadHash = CanonicalJson.computeHash(payload)

        // Mock pull response with 1 event
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"status":"ok","events":[{"event_id":"evt-pull-10","origin_node_id":"RENDER","logical_sequence":10,"event_type":"DEVICE_INVOCATION","payload":{"command":"vibrate"},"payload_hash":"$payloadHash","created_at":"2026-09-17T00:00:00Z"}],"cursor":10,"has_more":false}"""
                )
        )
        // Mock ACK response
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"status":"ok","acknowledged":["evt-pull-10"]}""")
        )

        assertEquals(0L, cursorStore.getCursor("RENDER"))

        val pulled = syncClient.pullIncoming()
        assertEquals(1, pulled)

        // Cursor MUST be updated to 10
        assertEquals(10L, cursorStore.getCursor("RENDER"))

        // Take pull request
        val pullReq = server.takeRequest()
        assertTrue(pullReq.path!!.contains("/api/sync/events/pull"))

        // Take ACK request
        val ackReq = server.takeRequest()
        assertEquals("/api/sync/events/ack", ackReq.path)
        assertTrue(ackReq.body.readUtf8().contains("evt-pull-10"))
    }

    @Test
    fun testPullDoesNotAdvanceCursorIfLocalDispatchFails() = runTest {
        val payload = buildJsonObject { put("command", "fail_action") }
        val payloadHash = CanonicalJson.computeHash(payload)

        // Custom client with failing eventDispatcher
        val failingClient = SyncClient(
            api = { ApiFactory.create(settings, settings.current.serverUrl) },
            settings = settings,
            outbox = outbox,
            inbox = inbox,
            cursorStore = cursorStore,
            eventDispatcher = { false } // Local dispatch rejects event!
        )

        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"status":"ok","events":[{"event_id":"evt-pull-99","origin_node_id":"RENDER","logical_sequence":99,"event_type":"DEVICE_INVOCATION","payload":{"command":"fail_action"},"payload_hash":"$payloadHash","created_at":"2026-09-17T00:00:00Z"}],"cursor":99,"has_more":false}"""
                )
        )

        assertEquals(0L, cursorStore.getCursor("RENDER"))
        val pulled = failingClient.pullIncoming()
        assertEquals(0, pulled)

        // INVARIANT: Cursor MUST NOT advance if local application failed!
        assertEquals("Cursor must remain at 0 if dispatch failed", 0L, cursorStore.getCursor("RENDER"))
    }
}
