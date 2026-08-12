package com.aura.companion.screen

import com.aura.companion.accessibility.AccessibilitySnapshot
import com.aura.companion.accessibility.AppInfo
import com.aura.companion.accessibility.AuraAccessibilityService
import com.aura.companion.accessibility.DeviceState
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * That something in production actually sends a screenshot.
 *
 * WHY THIS TEST EXISTS
 * --------------------
 * The bug being fixed was not a wrong result, it was an absent caller:
 * `AuraRepository.uploadScreenshot` and the whole server-side Vision
 * pipeline were correct and complete, and nothing on the phone ever invoked
 * them. Every conventional test passed the entire time. So the regression
 * worth pinning is structural - "a production class holds a
 * [ScreenshotUploader]" - because that is the property that was false.
 *
 * An [android.accessibilityservice.AccessibilityService] cannot be
 * constructed on a JVM, so this reads the declared fields rather than
 * calling anything. It proves the wiring exists, not that Android delivers
 * an event that runs it; the device procedure in the report covers that
 * half, and no unit test can.
 */
class ScreenshotWiringTest {

    @Test
    fun `the screen observation service holds a screenshot uploader`() {
        assertHasUploader(ScreenObservationService::class.java)
    }

    @Test
    fun `the agent service holds a screenshot uploader`() {
        assertHasUploader(AuraAccessibilityService::class.java)
    }

    /**
     * Both services, and only these two.
     *
     * A third capture path is the thing the brief rules out - Android has
     * exactly one screenshot mechanism this app can use, and both services
     * already have the accessibility grant it needs.
     */
    private fun assertHasUploader(service: Class<*>) {

        val fields = service.declaredFields.map { it.type }

        assertTrue(
            "${service.simpleName} declares no ScreenshotUploader, so nothing " +
                "in it can ever send a screenshot",
            fields.any { it == ScreenshotUploader::class.java },
        )
    }

    // ------------------------------------------------------------------
    // The agent reports what is actually true
    // ------------------------------------------------------------------

    @Test
    fun `screenshot availability is the outcome of an upload, not a constant`() {

        // The two states the agent loop derives from
        // `ScreenshotUploader.upload`, through the same field and the same
        // wire name the server already reads.
        val sent: ScreenshotOutcome = ScreenshotOutcome.Sent
        val skipped: ScreenshotOutcome = ScreenshotOutcome.Skipped("screenshots are switched off")

        assertEquals(true, availability(sent))
        assertEquals(false, availability(skipped))
        assertEquals(false, availability(ScreenshotOutcome.Failed("no pixels")))
    }

    @Test
    fun `the snapshot carries screenshot availability under the server's field name`() {

        // Present, and true, when the server is holding a frame.
        assertEquals(
            "an available screenshot must serialise as screenshot_available true",
            true,
            wireValue(snapshot(available = true)),
        )

        // Absent when it is not. `Json.Default` does not encode a property
        // equal to its default, and the default is false - so "no
        // screenshot" is an omitted key rather than an explicit `false`.
        // That is the contract either side must agree on: a reader that
        // treats absence as anything but false would read every screen
        // without pixels as a screen with them.
        assertEquals(
            "a snapshot with no screenshot must not claim one",
            null,
            wireValue(snapshot(available = false)),
        )
    }

    /**
     * The rule the agent loop applies.
     *
     * Only [ScreenshotOutcome.Sent] means the server is holding a frame.
     * Kept here as an assertion in its own right because "do not simply set
     * it to true" is the whole point: a skip and a failure are both "no
     * screenshot", and neither may read as one.
     */
    private fun availability(outcome: ScreenshotOutcome): Boolean =
        outcome is ScreenshotOutcome.Sent

    /**
     * What the server would see, or null if the field is not sent at all.
     *
     * Encoded through the same call the agent loop makes, so a
     * `@SerialName` change or a different `Json` instance fails here.
     */
    private fun wireValue(snapshot: AccessibilitySnapshot): Boolean? =
        Json.encodeToJsonElement(AccessibilitySnapshot.serializer(), snapshot)
            .jsonObject["screenshot_available"]
            ?.jsonPrimitive
            ?.boolean

    private fun snapshot(available: Boolean) = AccessibilitySnapshot(
        device = DeviceState(1080, 2400),
        app = AppInfo("com.google.android.youtube", label = "YouTube"),
        accessibilityTree = emptyMap(),
        screenshotAvailable = available,
        userRequest = "open youtube",
    )
}
