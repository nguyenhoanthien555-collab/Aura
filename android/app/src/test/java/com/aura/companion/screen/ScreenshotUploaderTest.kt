package com.aura.companion.screen

import com.aura.companion.data.AuraError
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.settings.FakeSettings
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/**
 * The half of screenshot upload a JVM can actually reach.
 *
 * Everything below the [ScreenshotCapture] seam is the framework asking a
 * `HardwareBuffer` for pixels, and no unit test can exercise that - a mock
 * of it would assert that a mock was called. What *is* testable is the part
 * that had the bug: whether anything sends a screenshot at all, whether the
 * privacy switches are honoured before pixels are read, and whether a
 * failure is visible instead of dropped.
 *
 * Run against a real HTTP server on loopback for the same reason
 * `AuraRepositoryTest` is: the failure mode this fix exists to prevent is
 * "the API is never called", and only a request arriving somewhere disproves
 * it.
 */
class ScreenshotUploaderTest {

    @get:Rule
    val cache = TemporaryFolder()

    private lateinit var server: MockWebServer
    private lateinit var settings: FakeSettings
    private lateinit var repository: AuraRepository

    /** Injected clock, so the interval is tested rather than waited out. */
    private var now = 1_000_000L

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()

        settings = FakeSettings(
            serverUrl = server.url("/").toString(),
            authToken = "test-token",
            screenObservationEnabled = true,
            uploadScreenshots = true,
        )

        repository = AuraRepository(settings)
    }

    @After
    fun tearDown() {
        // One test shuts the server down itself, to prove an unreachable
        // Aura is reported rather than thrown.
        runCatching { server.shutdown() }
    }

    // ------------------------------------------------------------------
    // There is a real caller, and it reaches the real route
    // ------------------------------------------------------------------

    @Test
    fun `a screenshot is posted to api slash screen slash upload as multipart`() = runTest {

        server.enqueue(accepted())

        val capture = FakeCapture(bytes = JPEG)

        val outcome = uploader(capture).upload("YouTube", "com.google.android.youtube")

        assertEquals(ScreenshotOutcome.Sent, outcome)
        assertEquals(1, capture.captures)

        val request = server.takeRequest()

        assertEquals("/api/screen/upload", request.path)
        assertEquals("POST", request.method)

        val contentType = request.getHeader("Content-Type").orEmpty()
        assertTrue("must be multipart: $contentType", contentType.startsWith("multipart/form-data"))

        assertEquals("Bearer test-token", request.getHeader("Authorization"))

        val body = request.body.readUtf8()

        // The server reads these as Form fields; a rename on either side is
        // exactly the kind of break a typed client cannot catch.
        assertTrue("device_id part missing", "name=\"device_id\"" in body)
        assertTrue("package part missing", "name=\"package\"" in body)
        assertTrue("application part missing", "name=\"application\"" in body)
        assertTrue("timestamp part missing", "name=\"timestamp\"" in body)
        assertTrue("screenshot part missing", "name=\"screenshot\"" in body)

        assertTrue("android-test" in body)
        assertTrue("com.google.android.youtube" in body)

        // ALLOWED_IMAGE_TYPES in server/routes/screen.py decides which
        // decoder runs, and it rejects anything it does not name.
        assertTrue("image/jpeg not declared", "image/jpeg" in body)
    }

    @Test
    fun `the captured bytes are what reach the wire`() = runTest {

        server.enqueue(accepted())

        uploader(FakeCapture(bytes = JPEG)).upload("YouTube", "com.google.android.youtube")

        val sent = server.takeRequest().body.readByteArray()

        assertTrue(
            "the JPEG bytes are not in the request body",
            sent.indexOfSlice(JPEG) >= 0,
        )
    }

    @Test
    fun `the screenshot is not left in the cache`() = runTest {

        server.enqueue(accepted())

        uploader(FakeCapture(bytes = JPEG)).upload("YouTube", "com.google.android.youtube")

        assertEquals(
            "a screenshot was left on disk",
            emptyList<String>(),
            cache.root.list()?.toList().orEmpty(),
        )
    }

    // ------------------------------------------------------------------
    // The switches are checked before the screen is read
    // ------------------------------------------------------------------

    @Test
    fun `screen observation off means nothing is captured`() = runTest {

        settings.setScreenObservation(false)

        val capture = FakeCapture(bytes = JPEG)

        val outcome = uploader(capture).upload("YouTube", "com.google.android.youtube")

        assertSkipped(outcome)
        assertEquals("the screen was read anyway", 0, capture.captures)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `screenshots off means nothing is captured`() = runTest {

        settings.setUploadScreenshots(false)

        val capture = FakeCapture(bytes = JPEG)

        val outcome = uploader(capture).upload("YouTube", "com.google.android.youtube")

        assertSkipped(outcome)
        assertEquals("the screen was read anyway", 0, capture.captures)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `no server means nothing is captured`() = runTest {

        settings.current = settings.current.copy(serverUrl = "")

        val capture = FakeCapture(bytes = JPEG)

        assertSkipped(uploader(capture).upload("YouTube", "com.google.android.youtube"))
        assertEquals(0, capture.captures)
    }

    @Test
    fun `an android version without the api is skipped, not attempted`() = runTest {

        val capture = FakeCapture(bytes = JPEG, isSupported = false)

        val outcome = uploader(capture).upload("YouTube", "com.google.android.youtube")

        assertSkipped(outcome)
        assertEquals(0, capture.captures)
        assertEquals(0, server.requestCount)
    }

    // ------------------------------------------------------------------
    // Not continuously
    // ------------------------------------------------------------------

    @Test
    fun `a second screenshot inside the interval is skipped`() = runTest {

        server.enqueue(accepted())

        val capture = FakeCapture(bytes = JPEG)
        val uploader = uploader(capture)

        assertEquals(ScreenshotOutcome.Sent, uploader.upload("YouTube", "com.pkg"))

        now += ScreenshotUploader.MIN_INTERVAL_MS - 1

        assertSkipped(uploader.upload("YouTube", "com.pkg"))

        assertEquals("captured twice inside the interval", 1, capture.captures)
        assertEquals(1, server.requestCount)
    }

    @Test
    fun `once the interval has passed another screenshot is sent`() = runTest {

        server.enqueue(accepted())
        server.enqueue(accepted())

        val capture = FakeCapture(bytes = JPEG)
        val uploader = uploader(capture)

        uploader.upload("YouTube", "com.pkg")

        now += ScreenshotUploader.MIN_INTERVAL_MS

        assertEquals(ScreenshotOutcome.Sent, uploader.upload("YouTube", "com.pkg"))
        assertEquals(2, capture.captures)
    }

    @Test
    fun `a failed attempt still costs the interval`() = runTest {

        // A server that is down would otherwise be re-captured on every
        // screen change, which is a full-screen encode per event for
        // nothing.
        server.enqueue(MockResponse().setResponseCode(500))

        val capture = FakeCapture(bytes = JPEG)
        val uploader = uploader(capture)

        assertTrue(uploader.upload("YouTube", "com.pkg") is ScreenshotOutcome.Failed)

        assertSkipped(uploader.upload("YouTube", "com.pkg"))
        assertEquals(1, capture.captures)
    }

    // ------------------------------------------------------------------
    // Failure is reported, not swallowed
    // ------------------------------------------------------------------

    @Test
    fun `a capture that produces nothing is a failure and never reaches the server`() = runTest {

        val outcome = uploader(FakeCapture(bytes = null)).upload("YouTube", "com.pkg")

        val failed = outcome as? ScreenshotOutcome.Failed
        assertNotNull("a failed capture must be reported", failed)

        // No AuraError: nothing was uploaded, so the server said nothing.
        assertEquals(null, failed?.error)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `an empty capture is a failure and never reaches the server`() = runTest {

        val outcome = uploader(FakeCapture(bytes = ByteArray(0))).upload("YouTube", "com.pkg")

        assertTrue(outcome is ScreenshotOutcome.Failed)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `a server that refuses the screenshot is reported with its own error`() = runTest {

        // The one 503 Aura itself sends: server.screen.enabled is false.
        server.enqueue(
            MockResponse()
                .setResponseCode(503)
                .setBody("""{"detail":{"error":"screen_disabled","message":"off"}}""")
        )

        val outcome = uploader(FakeCapture(bytes = JPEG)).upload("YouTube", "com.pkg")

        val failed = outcome as? ScreenshotOutcome.Failed
        assertNotNull("a refused upload must be reported", failed)

        assertTrue(
            "the typed error must survive: ${failed?.error}",
            failed?.error is AuraError.Unavailable,
        )

        assertEquals(failed?.error?.userMessage, failed?.reason)
    }

    @Test
    fun `a rejected image type is reported rather than retried`() = runTest {

        server.enqueue(MockResponse().setResponseCode(415))

        val outcome = uploader(FakeCapture(bytes = JPEG)).upload("YouTube", "com.pkg")

        assertTrue(outcome is ScreenshotOutcome.Failed)
        assertEquals(1, server.requestCount)
    }

    @Test
    fun `an unreachable server is reported and leaves nothing on disk`() = runTest {

        server.shutdown()

        val outcome = uploader(FakeCapture(bytes = JPEG)).upload("YouTube", "com.pkg")

        assertTrue(outcome is ScreenshotOutcome.Failed)
        assertEquals(emptyList<String>(), cache.root.list()?.toList().orEmpty())
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    private fun uploader(capture: ScreenshotCapture) = ScreenshotUploader(
        capture = capture,
        repository = repository,
        settings = settings,
        cacheDir = cache.root,
        clock = { now },
    )

    private fun assertSkipped(outcome: ScreenshotOutcome) {
        assertTrue("expected Skipped, got $outcome", outcome is ScreenshotOutcome.Skipped)
        assertTrue(
            "a skip must say why",
            (outcome as ScreenshotOutcome.Skipped).reason.isNotBlank(),
        )
    }

    private fun accepted() = MockResponse()
        .setHeader("Content-Type", "application/json")
        .setBody("""{"session_id":"s-1","status":"accepted","accepted":true,"size_bytes":4}""")

    /**
     * The seam, with the framework taken out of it.
     *
     * Counts captures rather than only recording the last one, because
     * "does not capture when the user said no" and "does not capture twice
     * in a row" are both assertions about how many times the screen was
     * read.
     */
    private class FakeCapture(
        private val bytes: ByteArray?,
        override val isSupported: Boolean = true,
    ) : ScreenshotCapture {

        var captures = 0
            private set

        override suspend fun capture(): ByteArray? {
            captures++
            return bytes
        }
    }

    private companion object {

        /** Not a real image; the phone never decodes it and neither does this. */
        val JPEG = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0x41, 0x42, 0xFF.toByte(), 0xD9.toByte())

        fun ByteArray.indexOfSlice(needle: ByteArray): Int {
            if (needle.isEmpty()) return 0
            outer@ for (start in 0..(size - needle.size)) {
                for (offset in needle.indices) {
                    if (this[start + offset] != needle[offset]) continue@outer
                }
                return start
            }
            return -1
        }
    }
}
