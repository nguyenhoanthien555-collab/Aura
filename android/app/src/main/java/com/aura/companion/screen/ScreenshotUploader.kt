package com.aura.companion.screen

import com.aura.companion.data.AuraError
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.AuraResult
import com.aura.companion.data.settings.SettingsProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.IOException

/**
 * What happened to one screenshot.
 *
 * Three outcomes rather than a boolean, because the three lead different
 * places: [Sent] is the only one that means the server is holding a frame,
 * [Skipped] is a rule working as intended and is not worth a log line at
 * warning level, and [Failed] is something the user would want to know
 * about if it kept happening.
 */
sealed interface ScreenshotOutcome {

    /** The server accepted the frame. */
    object Sent : ScreenshotOutcome

    /** Nothing was captured, and why. */
    data class Skipped(val reason: String) : ScreenshotOutcome

    /**
     * A capture or an upload was attempted and did not arrive.
     *
     * [error] is present when the *upload* failed, and null when the
     * capture never produced bytes to upload - the distinction between
     * "Aura could not be reached" and "this screen could not be read".
     */
    data class Failed(val reason: String, val error: AuraError? = null) : ScreenshotOutcome
}

/**
 * Decides whether to send a screenshot, and sends it.
 *
 * WHY THIS IS SEPARATE FROM THE SERVICES
 * --------------------------------------
 * Both accessibility services can capture, and both have to answer the
 * same four questions first: has the user allowed it, is there a server to
 * send it to, can this Android version do it at all, and was the last one
 * recent enough that this one is waste. Answering them twice is how the two
 * paths would drift apart. It is also the only part of screenshot upload
 * that a JVM test can reach, which is why the Android half sits behind
 * [ScreenshotCapture].
 *
 * THE GATES, IN ORDER
 * -------------------
 * `screenObservationEnabled` is checked *before* `uploadScreenshots`
 * because that is the promise the Privacy screen makes: screenshots are a
 * sub-switch of screen text, and with text off "nothing is sent at all".
 * Reading them in the other order would let a stale sub-switch send pixels
 * from a phone whose owner had turned observation off.
 *
 * The interval is stamped on every *attempt*, not on every success. A
 * server that is down would otherwise be re-captured on every screen
 * change, which costs the battery a full-screen encode each time for
 * nothing.
 */
class ScreenshotUploader(
    private val capture: ScreenshotCapture,
    private val repository: AuraRepository,
    private val settings: SettingsProvider,
    private val cacheDir: File,
    private val minIntervalMs: Long = MIN_INTERVAL_MS,
    private val clock: () -> Long = System::currentTimeMillis,
) {

    // Volatile rather than plain: the observation service calls this from a
    // coroutine on the default dispatcher and the agent from the main one,
    // so the field is not confined to a single thread the way
    // ObservationThrottle's is.
    @Volatile
    private var lastAttemptMs = 0L

    /**
     * Capture the current screen and upload it.
     *
     * Never throws. A screenshot is the optional half of an observation -
     * the text on the same screen has already been sent by the time this
     * runs - so a failure here is returned to be logged, not propagated
     * into the caller's flow.
     */
    suspend fun upload(application: String, packageName: String): ScreenshotOutcome {

        val current = settings.current

        if (!current.screenObservationEnabled) {
            return ScreenshotOutcome.Skipped("screen observation is off")
        }

        if (!current.uploadScreenshots) {
            return ScreenshotOutcome.Skipped("screenshots are switched off")
        }

        if (!current.isConfigured) {
            return ScreenshotOutcome.Skipped("no server is configured")
        }

        if (!capture.isSupported) {
            return ScreenshotOutcome.Skipped(
                "this Android version cannot take a screenshot"
            )
        }

        val now = clock()

        if (now - lastAttemptMs < minIntervalMs) {
            return ScreenshotOutcome.Skipped("the last screenshot was too recent")
        }

        lastAttemptMs = now

        val bytes = capture.capture()
            ?: return ScreenshotOutcome.Failed("the screen could not be captured")

        if (bytes.isEmpty()) {
            return ScreenshotOutcome.Failed("the capture produced no pixels")
        }

        return withContext(Dispatchers.IO) { send(bytes, application, packageName) }
    }

    /**
     * Hand the bytes to the repository.
     *
     * Via a file because that is the shape `uploadScreenshot` already
     * takes, and rewriting the API to accept a `ByteArray` would change a
     * working multipart request for no gain. The file is unique per call so
     * two services capturing at once cannot overwrite each other, and it is
     * deleted on every path - a screenshot is the one thing in this app
     * that must not be left sitting in cache.
     */
    private suspend fun send(
        bytes: ByteArray,
        application: String,
        packageName: String,
    ): ScreenshotOutcome {

        val file = try {
            File.createTempFile(FILE_PREFIX, FILE_SUFFIX, cacheDir).also {
                it.writeBytes(bytes)
            }
        } catch (error: IOException) {
            return ScreenshotOutcome.Failed("the screenshot could not be written to cache")
        }

        return try {
            when (val result = repository.uploadScreenshot(file, application, packageName)) {
                is AuraResult.Ok -> ScreenshotOutcome.Sent
                is AuraResult.Failed -> ScreenshotOutcome.Failed(
                    result.error.userMessage,
                    result.error,
                )
            }
        } finally {
            file.delete()
        }
    }

    companion object {

        /**
         * Minimum gap between two screenshots.
         *
         * Matched to `server.screen.min_interval` (8.0s), which is what
         * `VisionManager` throttles on: a frame that arrives inside that
         * window replaces the last one in `RemoteScreenSource` without ever
         * being described, so uploading faster than this spends data to
         * overwrite something the server had not looked at yet.
         */
        const val MIN_INTERVAL_MS = 8_000L

        private const val FILE_PREFIX = "aura-screenshot"

        /** Must stay `.jpg`: `uploadScreenshot` declares `image/jpeg`. */
        private const val FILE_SUFFIX = ".jpg"
    }
}
