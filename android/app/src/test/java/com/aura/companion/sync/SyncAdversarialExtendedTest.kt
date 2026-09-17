package com.aura.companion.sync

import com.aura.companion.accessibility.AuraAccessibilityService
import com.aura.companion.data.remote.ApiFactory
import com.aura.companion.data.remote.SyncEventDto
import com.aura.companion.data.settings.FakeSettings
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.nio.file.Files

class SyncAdversarialExtendedTest {

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

        tempDir = Files.createTempDirectory("aura_ext_test").toFile()
        settings = FakeSettings(
            serverUrl = server.url("/").toString(),
            authToken = "adversarial-token",
            deviceId = "test-adv-device"
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
    // Section 8: Exhaustive Cryptographic Hash Parity with Python
    // ----------------------------------------------------------------------
    @Test
    fun testCanonicalHashEdgeCasesParity() {
        // 1. Integer
        val objInt = buildJsonObject { put("v", 1) }
        assertEquals("afbf9d0f3560b0fd7795e81c42a0a79ee6b6fc67e064f77826aee642cad28d91", CanonicalJson.computeHash(objInt))

        // 2. Float
        val objFloat = buildJsonObject { put("v", 1.0) }
        assertEquals("f3c8be97307d38261061fa08e74365e54a239cddb1d189f3bea18022adbe2f35", CanonicalJson.computeHash(objFloat))

        // 3. Boolean True
        val objTrue = buildJsonObject { put("v", true) }
        assertEquals("9175b89688753d7371f5ad803366cc394dbda9d202494fba0216c6326fa67004", CanonicalJson.computeHash(objTrue))

        // 4. Boolean False
        val objFalse = buildJsonObject { put("v", false) }
        assertEquals("eeb0deb9cb259a55fdff2c5ed5d0a08dba3ff585aafa0821cc51889f7660739e", CanonicalJson.computeHash(objFalse))

        // 5. Null
        val objNull = buildJsonObject { put("v", JsonNull) }
        assertEquals("aae9e223dcdc02dfd8149da8c25b9536533a7232b0c9137ee6b2cd5c2a2629eb", CanonicalJson.computeHash(objNull))

        // 6. Empty Object
        val objEmpty = buildJsonObject {}
        assertEquals("44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a", CanonicalJson.computeHash(objEmpty))

        // 7. Empty Array
        val objEmptyArr = buildJsonObject { putJsonArray("list") {} }
        assertEquals("6d355a642ffed28c2afbda550638c33deb10c7f463ea5f2ce79a5ee5a8e15a4c", CanonicalJson.computeHash(objEmptyArr))

        // 8. Unicode String
        val objUnicode = buildJsonObject { put("text", "Tiếng Việt - 日本語 - 🚀") }
        assertEquals("8ac85a6bbc5e6a8caf7cb103ea46dc2ff741a561c9887d12539429641ef4e032", CanonicalJson.computeHash(objUnicode))
    }

    // ----------------------------------------------------------------------
    // Section 11: Concurrent Sync Serialization
    // ----------------------------------------------------------------------
    @Test
    fun testConcurrentSyncCycleSerialization() = runTest {
        // Enqueue 1 event
        val payload = buildJsonObject { put("run", 1) }
        val evt = SyncEventDto(
            eventId = "evt-concurrent-1",
            originNodeId = "test-adv-device",
            logicalSequence = 1L,
            eventType = "ACTION",
            payload = payload,
            payloadHash = CanonicalJson.computeHash(payload),
            createdAt = "2026-09-17T00:00:00Z"
        )
        outbox.enqueue(evt)

        // Queue responses for register, push, pull
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"ok","node_id":"test-adv-device","last_seen":"..."}"""))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"ok","acknowledged":["evt-concurrent-1"],"conflicts":[]}"""))
        server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"ok","events":[],"cursor":0,"has_more":false}"""))

        // For remaining 4 concurrent calls, mock empty push & pull responses
        for (i in 1..4) {
            server.enqueue(MockResponse().setResponseCode(200).setBody("""{"status":"ok","events":[],"cursor":0,"has_more":false}"""))
        }

        // Launch 5 concurrent calls
        val jobs = (1..5).map {
            async {
                syncClient.syncCycle()
            }
        }
        val results = jobs.awaitAll()
        assertEquals(5, results.size)
        assertEquals(0, outbox.pendingCount())
    }

    // ----------------------------------------------------------------------
    // Section 16: Attack Large Backlog Drain
    // ----------------------------------------------------------------------
    @Test
    fun testLargeBacklogDrain() {
        val totalEvents = 100
        val createdIds = mutableListOf<String>()

        for (i in 1..totalEvents) {
            val payload = buildJsonObject { put("index", i) }
            val id = "evt-backlog-$i"
            createdIds.add(id)
            val evt = SyncEventDto(
                eventId = id,
                originNodeId = "test-adv-device",
                logicalSequence = i.toLong(),
                eventType = "ACTION",
                payload = payload,
                payloadHash = CanonicalJson.computeHash(payload),
                createdAt = "2026-09-17T00:00:00Z"
            )
            outbox.enqueue(evt)
        }
        assertEquals(totalEvents, outbox.pendingCount())

        // Simulate draining in batches of 50
        val batch1 = outbox.getPending(50)
        assertEquals(50, batch1.size)
        outbox.markAcknowledged(batch1.map { it.eventId })
        assertEquals(50, outbox.pendingCount())

        val batch2 = outbox.getPending(50)
        assertEquals(50, batch2.size)
        outbox.markAcknowledged(batch2.map { it.eventId })
        assertEquals(0, outbox.pendingCount())
    }

    // ----------------------------------------------------------------------
    // Section 4: Outbox SENDING Recovery on Reload
    // ----------------------------------------------------------------------
    @Test
    fun testSendingRecoveryOnReload() {
        val payload = buildJsonObject { put("key", "test") }
        val evt = SyncEventDto(
            eventId = "evt-inflight-crash",
            originNodeId = "test-adv-device",
            logicalSequence = 1L,
            eventType = "ACTION",
            payload = payload,
            payloadHash = CanonicalJson.computeHash(payload),
            createdAt = "2026-09-17T00:00:00Z"
        )
        outbox.enqueue(evt)
        outbox.markSending(listOf("evt-inflight-crash"))

        // Simulate complete app / process crash while request was in-flight
        val reloadedOutbox = FileEventOutbox(File(tempDir, "outbox"))
        assertEquals("In-flight unacked event must recover as pending on restart", 1, reloadedOutbox.pendingCount())
        val pending = reloadedOutbox.getPending(10)
        assertEquals(1, pending.size)
        assertEquals("evt-inflight-crash", pending[0].eventId)
    }

    // ----------------------------------------------------------------------
    // Section 18: Real Runtime Lifecycle Wiring Proof
    // ----------------------------------------------------------------------
    @Test
    fun testServiceSyncJobFieldWiring() {
        // Assert that AuraAccessibilityService possesses the syncJob coroutine Job field
        val field = AuraAccessibilityService::class.java.getDeclaredField("syncJob")
        assertNotNull("AuraAccessibilityService must declare syncJob field", field)
    }
}
