package com.aura.companion.accessibility

import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The open_app verification race: `startActivity` returns before the
 * target activity becomes the foreground accessibility package, and a
 * single immediate snapshot read the previous app ("expected YouTube,
 * got Aura"), returned UNVERIFIED, and the agent loop re-issued the same
 * launch. `waitForForegroundPackage` replaces the single snapshot with
 * bounded polling; these tests drive its timing deterministically with
 * an injected clock and sleep so they never touch real time.
 *
 * (Named [OpenAppForegroundPollingTest] rather than reusing
 * OpenAppVerificationTest, which already exists in AgentActionParserTest
 * and covers the static `verifyOpenApp` identity rule.)
 */
class OpenAppForegroundPollingTest {

    private class FakeClock {
        var now: Long = 0
    }

    @Test
    fun targetAlreadyForegroundSucceedsImmediately() = runBlocking {
        val clock = FakeClock()
        var samples = 0

        val verified = AuraAccessibilityService.waitForForegroundPackage(
            target = "com.google.android.youtube",
            currentPackage = {
                samples++
                "com.google.android.youtube"
            },
            timeoutMs = 2000,
            intervalMs = 100,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        assertTrue(verified)
        // One sample, no waiting, no relaunch window: an app that is
        // already foreground must not be launched again.
        assertEquals(1, samples)
        assertEquals(0L, clock.now)
    }

    @Test
    fun targetAppearsAfterDelayIsEventuallyVerified() = runBlocking {
        val clock = FakeClock()
        val arrivesAtMs = 500L

        val verified = AuraAccessibilityService.waitForForegroundPackage(
            target = "com.google.android.youtube",
            currentPackage = {
                // The launch in progress: still the previous app until
                // the target draws at 500ms.
                if (clock.now >= arrivesAtMs) "com.google.android.youtube"
                else "com.aura.companion"
            },
            timeoutMs = 2000,
            intervalMs = 100,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        assertTrue(verified)
        // Samples at 0,100,200,300,400 read the old app and are waited
        // through; the one at 500 verifies. Never a failure mid-launch.
        assertTrue(clock.now in 400L..600L)
    }

    @Test
    fun targetNeverAppearsTimesOutBounded() = runBlocking {
        val clock = FakeClock()

        val verified = AuraAccessibilityService.waitForForegroundPackage(
            target = "com.google.android.youtube",
            currentPackage = { "com.aura.companion" },
            timeoutMs = 2000,
            intervalMs = 100,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        assertFalse(verified)
        // Bounded: the budget is spent, and at most one interval of
        // overshoot - it never waits forever.
        assertTrue(clock.now in 2000L..2100L)
    }

    @Test
    fun blankTargetNeverPolls() = runBlocking {
        val clock = FakeClock()
        var samples = 0

        val verified = AuraAccessibilityService.waitForForegroundPackage(
            target = "",
            currentPackage = {
                samples++
                "com.aura.companion"
            },
            timeoutMs = 2000,
            intervalMs = 100,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        assertFalse(verified)
        assertEquals(0, samples)
        assertEquals(0L, clock.now)
    }

    @Test
    fun slowLaunchIsWaitedThroughNotFailedImmediately() = runBlocking {
        // A cold start at 2200ms is beyond a normal launch but inside the
        // budget: the poll must still succeed, and the loop must never
        // have reported failure in between.
        val clock = FakeClock()
        val arrivesAtMs = 2200L

        val verified = AuraAccessibilityService.waitForForegroundPackage(
            target = "com.slow.app",
            currentPackage = {
                if (clock.now >= arrivesAtMs) "com.slow.app" else "com.aura.companion"
            },
            timeoutMs = 2500,
            intervalMs = 150,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        assertTrue(verified)
        assertTrue(clock.now in 2100L..2300L)
    }
}
