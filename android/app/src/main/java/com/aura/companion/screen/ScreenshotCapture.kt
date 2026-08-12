package com.aura.companion.screen

import android.accessibilityservice.AccessibilityService
import android.graphics.Bitmap
import android.os.Build
import android.util.Log
import android.view.Display
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executor
import kotlin.coroutines.resume
import kotlin.math.sqrt

/**
 * The pixels of the current screen, as JPEG bytes.
 *
 * This exists as an interface for one reason: [ScreenshotUploader] decides
 * *whether* to send a screenshot, and that decision is the part worth
 * testing. The Android side of it - a framework callback delivering a
 * `HardwareBuffer` - cannot be exercised on a JVM at all, so it sits behind
 * this seam rather than being mocked into something that proves nothing.
 */
interface ScreenshotCapture {

    /**
     * Whether this device can produce a screenshot.
     *
     * A capability, not a permission: false here means the API does not
     * exist on this Android version, so no setting and no consent can make
     * a capture happen. Checked before anything is read from the screen.
     */
    val isSupported: Boolean

    /**
     * JPEG bytes of the current display, or null if the capture failed.
     *
     * Suspending because the framework answers asynchronously, and
     * cancellable because the caller is a service whose scope dies with
     * it.
     */
    suspend fun capture(): ByteArray?
}

/**
 * [ScreenshotCapture] backed by the accessibility service itself.
 *
 * WHY THIS MECHANISM
 * ------------------
 * `AccessibilityService.takeScreenshot` is the only screenshot API this app
 * can reach without becoming a different kind of app. The alternative -
 * `MediaProjection` - needs a consent dialog per session and a foreground
 * service with a permanent notification, and Aura already *is* two
 * accessibility services with the user's explicit grant. Using the grant
 * that exists beats asking for a second one.
 *
 * WHAT IT COSTS
 * -------------
 * The capability has to be declared (`android:canTakeScreenshot` in both
 * `res/xml` service configs), the API arrived in Android 11, and the
 * framework rate-limits calls of its own accord -
 * `ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT`. [ScreenshotUploader]'s
 * interval is far above that floor, so hitting it means something else is
 * capturing too.
 *
 * On Android 10 and below [isSupported] is false and nothing is captured.
 * That is a real device state, and it is what makes `screenshot_available`
 * in the agent snapshot worth reading rather than assumed.
 */
class AccessibilityScreenshotCapture(
    private val service: AccessibilityService,
) : ScreenshotCapture {

    override val isSupported: Boolean
        get() = Build.VERSION.SDK_INT >= Build.VERSION_CODES.R

    override suspend fun capture(): ByteArray? {

        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null

        val bitmap = takeScreenshot() ?: return null

        // Encoding a full-screen bitmap is tens of milliseconds of CPU. The
        // agent loop runs on the main dispatcher, so it does not happen
        // there.
        return withContext(Dispatchers.Default) { encode(bitmap) }
    }

    /**
     * One frame from the framework, or null and a logged reason.
     *
     * The `HardwareBuffer` is closed on every path. It is a graphics
     * allocation the size of the screen, and leaking one per observation
     * would exhaust the buffer pool long before the user noticed anything
     * else was wrong.
     */
    private suspend fun takeScreenshot(): Bitmap? =
        suspendCancellableCoroutine { continuation ->

            val callback = object : AccessibilityService.TakeScreenshotCallback {

                override fun onSuccess(result: AccessibilityService.ScreenshotResult) {

                    val bitmap = runCatching {
                        // wrapHardwareBuffer gives a HARDWARE bitmap, which a
                        // software Canvas refuses to draw and which keeps the
                        // buffer alive. Copy once, close, and everything after
                        // this is ordinary pixels.
                        Bitmap.wrapHardwareBuffer(result.hardwareBuffer, result.colorSpace)
                            ?.let { hardware ->
                                val software = hardware.copy(Bitmap.Config.ARGB_8888, false)
                                hardware.recycle()
                                software
                            }
                    }.getOrNull()

                    runCatching { result.hardwareBuffer.close() }

                    if (continuation.isActive) {
                        continuation.resume(bitmap)
                    } else {
                        bitmap?.recycle()
                    }
                }

                override fun onFailure(errorCode: Int) {
                    Log.w(TAG, "takeScreenshot failed: ${reason(errorCode)}")
                    if (continuation.isActive) continuation.resume(null)
                }
            }

            val started = runCatching {
                service.takeScreenshot(Display.DEFAULT_DISPLAY, DIRECT_EXECUTOR, callback)
            }

            // A SecurityException here means the capability is not declared
            // or the service is not currently bound. Reported rather than
            // thrown: a screenshot is optional, and the text observation on
            // the same event is not.
            started.exceptionOrNull()?.let { error ->
                Log.w(TAG, "takeScreenshot refused: ${error.javaClass.simpleName}")
                if (continuation.isActive) continuation.resume(null)
            }
        }

    /**
     * JPEG, scaled to what the server would keep anyway.
     *
     * `vision.max_pixels` (1_500_000) is the bound the cloud processor
     * downscales to before it encodes, so a phone that sends more than that
     * pays for pixels the server throws away - on a metered connection,
     * every time. Mirrored here for that reason and no other; the server
     * remains the authority and still applies its own bound.
     */
    private fun encode(source: Bitmap): ByteArray? {

        val bitmap = scaled(source)

        return try {
            ByteArrayOutputStream().use { out ->
                val ok = bitmap.compress(Bitmap.CompressFormat.JPEG, QUALITY, out)
                if (ok) out.toByteArray() else null
            }
        } catch (error: Exception) {
            Log.w(TAG, "screenshot encode failed: ${error.javaClass.simpleName}")
            null
        } finally {
            bitmap.recycle()
            if (bitmap !== source) source.recycle()
        }
    }

    private fun scaled(bitmap: Bitmap): Bitmap {

        val pixels = bitmap.width.toLong() * bitmap.height.toLong()

        if (pixels <= MAX_PIXELS || pixels <= 0L) return bitmap

        val factor = sqrt(MAX_PIXELS.toDouble() / pixels.toDouble())

        val width = (bitmap.width * factor).toInt().coerceAtLeast(1)
        val height = (bitmap.height * factor).toInt().coerceAtLeast(1)

        return runCatching {
            Bitmap.createScaledBitmap(bitmap, width, height, true)
        }.getOrDefault(bitmap)
    }

    private fun reason(errorCode: Int): String = when (errorCode) {
        AccessibilityService.ERROR_TAKE_SCREENSHOT_INTERNAL_ERROR -> "internal error"
        AccessibilityService.ERROR_TAKE_SCREENSHOT_NO_ACCESSIBILITY_ACCESS -> "no accessibility access"
        AccessibilityService.ERROR_TAKE_SCREENSHOT_INTERVAL_TIME_SHORT -> "asked again too soon"
        AccessibilityService.ERROR_TAKE_SCREENSHOT_INVALID_DISPLAY -> "invalid display"
        else -> "code $errorCode"
    }

    private companion object {

        const val TAG = "AuraScreenshot"

        /** Kept in step with `vision.max_pixels` in config.yaml. */
        const val MAX_PIXELS = 1_500_000L

        /**
         * The server re-encodes at 75 after its own downscale, so anything
         * higher here is bytes on the wire that do not survive the trip.
         */
        const val QUALITY = 80

        /**
         * Runs the framework callback on whichever thread delivered it.
         *
         * All the callback does is resume a continuation, which the
         * coroutine machinery dispatches correctly from anywhere. A thread
         * pool for that would be one more thing to shut down when the
         * service dies.
         */
        val DIRECT_EXECUTOR = Executor { it.run() }
    }
}
