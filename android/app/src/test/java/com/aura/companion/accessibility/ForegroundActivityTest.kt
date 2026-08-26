package com.aura.companion.accessibility

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * `AppInfo.activity` was declared, serialized, read by two server
 * modules - and never assigned. Nothing on the device ever wrote it, so
 * `focus.screen` arrived empty on every tick of every session, and both
 * `brain/task_graph.py` and `brain/recovery.py` restrict what they will
 * verify with a comment naming this field as the reason.
 *
 * The server side was never the gap: `tests/test_machine_turns.py`
 * already asserts `state.focus.screen == ".Main"` from a hand-built
 * payload carrying `"activity": ".Main"`. A passing test built on a field
 * production cannot fill is the defect, not the coverage.
 *
 * These cover the two decisions the fix rests on. Both live in the
 * companion object because an [android.accessibilityservice.AccessibilityService]
 * cannot be constructed in a JVM unit test.
 */
class ForegroundActivityTest {

    private val youtube = "com.google.android.youtube"
    private val chrome = "com.android.chrome"

    // windowFrom - what is worth remembering
    // ------------------------------------------------------------------

    @Test
    fun aNamedWindowIsRemembered() {
        val window = AuraAccessibilityService.windowFrom(
            youtube,
            "com.google.android.youtube.HomeActivity",
        )

        assertEquals(youtube, window?.packageName)
        assertEquals("com.google.android.youtube.HomeActivity", window?.className)
    }

    @Test
    fun aWindowWithNoPackageIsNotRemembered() {
        assertNull(AuraAccessibilityService.windowFrom(null, "SomeActivity"))
        assertNull(AuraAccessibilityService.windowFrom("", "SomeActivity"))
        assertNull(AuraAccessibilityService.windowFrom("   ", "SomeActivity"))
    }

    @Test
    fun aWindowWithNoClassIsNotRemembered() {
        assertNull(AuraAccessibilityService.windowFrom(youtube, null))
        assertNull(AuraAccessibilityService.windowFrom(youtube, ""))
        assertNull(AuraAccessibilityService.windowFrom(youtube, "   "))
    }

    /**
     * The reason a blank cannot be allowed through as a Window: the
     * caller keeps the previous one when this returns null, and a blank
     * screen reaching the server would assert "nothing there" where the
     * truth is "no news". Those are different values with different
     * consequences in `CognitiveState.observe`.
     */
    @Test
    fun aBlankEventLeavesTheLastKnownScreenAlone() {
        val known = AuraAccessibilityService.windowFrom(youtube, "HomeActivity")

        val next = AuraAccessibilityService.windowFrom(youtube, "  ")

        assertNull(next)
        assertEquals("HomeActivity", known?.className)
    }

    @Test
    fun surroundingWhitespaceIsNotPartOfTheName() {
        val window = AuraAccessibilityService.windowFrom(
            "  $youtube  ",
            "  HomeActivity  ",
        )

        assertEquals(youtube, window?.packageName)
        assertEquals("HomeActivity", window?.className)
    }

    // activityFor - what is safe to report
    // ------------------------------------------------------------------

    @Test
    fun theScreenIsReportedForTheAppItWasSeenIn() {
        val window = AuraAccessibilityService.Window(youtube, "HomeActivity")

        assertEquals(
            "HomeActivity",
            AuraAccessibilityService.activityFor(window, youtube),
        )
    }

    /**
     * The case worth the guard. A launch the platform has not yet named
     * with a window-state change leaves the remembered window pointing at
     * the app we came from; reporting it would tell the server Chrome is
     * in the foreground showing YouTube's home screen, which was true at
     * no instant.
     */
    @Test
    fun aScreenFromAnotherAppIsNeverReported() {
        val window = AuraAccessibilityService.Window(youtube, "HomeActivity")

        assertNull(AuraAccessibilityService.activityFor(window, chrome))
    }

    @Test
    fun nothingIsReportedBeforeAnyWindowHasBeenSeen() {
        assertNull(AuraAccessibilityService.activityFor(null, youtube))
    }

    @Test
    fun theForegroundPackageIsComparedWithoutSurroundingWhitespace() {
        val window = AuraAccessibilityService.Window(youtube, "HomeActivity")

        assertEquals(
            "HomeActivity",
            AuraAccessibilityService.activityFor(window, "  $youtube  "),
        )
    }

    // The wire contract
    // ------------------------------------------------------------------

    /**
     * The key the server actually reads.
     *
     * `brain/agent_mode.py` reads `context["app"]["activity"]` and
     * `brain/prompt_builder.py` reads the same key. `packageName` carries
     * a `@SerialName("package")` and `activity` deliberately does not, so
     * a rename on either side would leave the field permanently absent
     * again - the exact state this phase repaired - and every test above
     * would still pass, because they never cross the serializer.
     */
    @Test
    fun theActivityCrossesTheWireUnderTheKeyTheServerReads() {
        val app = AppInfo(
            packageName = youtube,
            activity = "com.google.android.youtube.HomeActivity",
            label = "YouTube",
        )

        val encoded = Json.encodeToJsonElement(app).jsonObject

        assertEquals(
            "com.google.android.youtube.HomeActivity",
            encoded["activity"]?.toString()?.trim('"'),
        )
        assertEquals(youtube, encoded["package"]?.toString()?.trim('"'))
    }

    /**
     * And the server reads it off `app`, not off the snapshot root.
     */
    @Test
    fun theActivitySitsUnderAppInTheSnapshot() {
        val snapshot = AccessibilitySnapshot(
            device = DeviceState(width = 1080, height = 2400),
            app = AppInfo(
                packageName = youtube,
                activity = "HomeActivity",
                label = "YouTube",
            ),
            accessibilityTree = emptyMap(),
        )

        val app = Json.encodeToJsonElement(snapshot).jsonObject["app"]

        assertEquals(
            "HomeActivity",
            app?.jsonObject?.get("activity")?.toString()?.trim('"'),
        )
    }
}
