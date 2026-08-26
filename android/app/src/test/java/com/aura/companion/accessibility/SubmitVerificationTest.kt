package com.aura.companion.accessibility

import com.aura.companion.accessibility.AuraAccessibilityService.ScreenFingerprint
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The submit verification race, which is the open_app race one action
 * later in the same flow.
 *
 * `submit` fires the IME action and returns; the results it asked for
 * then arrive over the network. Verification used to be a single
 * snapshot 250ms afterwards, which on any real search is still the
 * unchanged query screen - so a submit that worked was reported
 * UNVERIFIED, and the agent spent one of ten steps re-submitting a
 * search that was already running.
 *
 * `waitForContentChange` polls instead, bounded, exiting the moment the
 * screen moves. The clock and sleep are injected so these tests never
 * touch real time.
 */
class SubmitVerificationTest {

    private class FakeClock {
        var now: Long = 0
    }

    private fun screen(
        pkg: String = "com.google.android.youtube",
        nodes: Int = 40,
        hash: Int = 1000,
    ) = ScreenFingerprint(packageName = pkg, nodeCount = nodes, contentHash = hash)

    @Test
    fun resultsAlreadyRenderedSucceedImmediately() = runBlocking {
        val clock = FakeClock()
        var samples = 0

        val verified = AuraAccessibilityService.waitForContentChange(
            pre = screen(hash = 1000),
            currentFingerprint = {
                samples++
                screen(hash = 2000)
            },
            timeoutMs = 3000,
            intervalMs = 150,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        assertTrue(verified)
        // A screen that has already moved must not be waited on: the
        // budget exists for slow results, not as a fixed toll.
        assertEquals(1, samples)
        assertEquals(0L, clock.now)
    }

    @Test
    fun resultsArrivingLateAreStillVerified() = runBlocking {
        val clock = FakeClock()
        val arrivesAtMs = 900L

        val verified = AuraAccessibilityService.waitForContentChange(
            pre = screen(hash = 1000),
            currentFingerprint = {
                // The query screen is still up until the results draw.
                // This is the exact window the old single snapshot at
                // 250ms landed in.
                if (clock.now >= arrivesAtMs) screen(hash = 2000) else screen(hash = 1000)
            },
            timeoutMs = 3000,
            intervalMs = 150,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        assertTrue(verified)
        assertTrue("should not have returned before the results drew", clock.now >= arrivesAtMs)
        assertTrue("should not have burned the whole budget", clock.now < 3000)
    }

    @Test
    fun aScreenThatNeverMovesIsHonestlyUnverified() = runBlocking {
        val clock = FakeClock()

        val verified = AuraAccessibilityService.waitForContentChange(
            pre = screen(hash = 1000),
            currentFingerprint = { screen(hash = 1000) },
            timeoutMs = 3000,
            intervalMs = 150,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        // A submit into a box that produced nothing really did fail, and
        // saying so is what lets recovery try a different route. The
        // point of the poll is to stop reporting *working* submits as
        // failures, not to start reporting failures as successes.
        assertFalse(verified)
        assertTrue("must be bounded", clock.now in 3000..3300)
    }

    @Test
    fun aNodeCountChangeCounts() = runBlocking {
        val clock = FakeClock()

        val verified = AuraAccessibilityService.waitForContentChange(
            pre = screen(nodes = 40, hash = 1000),
            // Results whose text hashes to the same value but which add
            // rows: unlikely, and free to accept. Any of the three
            // fingerprint fields moving is evidence the screen responded.
            currentFingerprint = { screen(nodes = 55, hash = 1000) },
            timeoutMs = 3000,
            intervalMs = 150,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        assertTrue(verified)
    }

    @Test
    fun aPackageChangeCounts() = runBlocking {
        val clock = FakeClock()

        val verified = AuraAccessibilityService.waitForContentChange(
            pre = screen(pkg = "com.android.chrome", hash = 1000),
            // Submitting in a browser omnibox can hand off to another
            // app entirely. The screen moved; that is the question.
            currentFingerprint = { screen(pkg = "com.google.android.youtube", hash = 1000) },
            timeoutMs = 3000,
            intervalMs = 150,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        assertTrue(verified)
    }

    @Test
    fun anUnreadableScreenIsWaitedThroughNotFailed() = runBlocking {
        val clock = FakeClock()
        var samples = 0

        val verified = AuraAccessibilityService.waitForContentChange(
            pre = screen(hash = 1000),
            currentFingerprint = {
                samples++
                // rootInActiveWindow is null during a window transition,
                // which is precisely when a submit is working. Treating
                // that as failure would reintroduce the bug through a
                // different door.
                if (samples < 3) null else screen(hash = 2000)
            },
            timeoutMs = 3000,
            intervalMs = 150,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        assertTrue(verified)
        assertEquals(3, samples)
    }

    @Test
    fun anImpossibleBudgetDoesNotHang() = runBlocking {
        val clock = FakeClock()

        val verified = AuraAccessibilityService.waitForContentChange(
            pre = screen(hash = 1000),
            currentFingerprint = { screen(hash = 1000) },
            timeoutMs = 0,
            intervalMs = 150,
            now = { clock.now },
            sleep = { clock.now += it },
        )

        assertFalse(verified)
        assertEquals(0L, clock.now)
    }
}
