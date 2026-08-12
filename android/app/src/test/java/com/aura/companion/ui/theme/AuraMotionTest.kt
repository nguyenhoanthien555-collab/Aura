package com.aura.companion.ui.theme

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The two motion decisions that are not cosmetic.
 *
 * Everything else in [AuraMotion] is taste, and taste does not need a test.
 * These two are promises to someone who asked the phone for something:
 *
 *   [AuraMotion.scaled]   reduced motion means *no* motion, not less of it.
 *                         Someone who turns animations off has either an
 *                         accessibility reason or a nearly flat battery, and
 *                         a halved duration answers neither of them.
 *
 *   [AuraMotion.mayLoop]  a repeating animation keeps the frame pipeline
 *                         awake for as long as the screen is on, so it runs
 *                         only while something is genuinely in flight.
 *
 * Both are plain functions rather than `@Composable`s precisely so they can
 * be asserted here - this module has no JVM Compose harness and no
 * Robolectric, so a decision inside a composable is a decision nothing checks.
 */
class AuraMotionTest {

    @Test
    fun `reduced motion is instant, not merely faster`() {

        // 0, not `Quick / 2`. `tween(0)` is "already finished", which is the
        // whole request: the state change still happens, it just does not
        // travel.
        assertEquals(0, AuraMotion.scaled(AuraMotion.Quick, reduced = true))
        assertEquals(0, AuraMotion.scaled(AuraMotion.Standard, reduced = true))
        assertEquals(0, AuraMotion.scaled(AuraMotion.Slow, reduced = true))
    }

    @Test
    fun `a phone that wants motion gets the duration unchanged`() {

        assertEquals(AuraMotion.Standard, AuraMotion.scaled(AuraMotion.Standard, reduced = false))
        assertEquals(1, AuraMotion.scaled(1, reduced = false))
    }

    @Test
    fun `the scale is three distinct steps, in order`() {

        // A shared scale is what makes two different surfaces read as the
        // same app moving. Collapsing two of these together would leave call
        // sites free to drift apart again.
        assertTrue(
            "${AuraMotion.Quick} < ${AuraMotion.Standard} < ${AuraMotion.Slow}",
            AuraMotion.Quick < AuraMotion.Standard && AuraMotion.Standard < AuraMotion.Slow,
        )

        assertTrue("no duration may be instant by default", AuraMotion.Quick > 0)
    }

    @Test
    fun `a loop runs only while something is actually in flight`() {

        assertTrue(AuraMotion.mayLoop(reduced = false, busy = true))

        // Settled: the ring has nothing left to say, so it stops.
        assertTrue("a settled state must not loop", !AuraMotion.mayLoop(reduced = false, busy = false))
    }

    @Test
    fun `reduced motion outranks busy`() {

        // The one combination worth stating on its own: "busy" is Aura's
        // reason to animate, and it does not get to overrule the phone's.
        assertTrue(
            "reduced motion must win even mid-request",
            !AuraMotion.mayLoop(reduced = true, busy = true),
        )
    }
}
