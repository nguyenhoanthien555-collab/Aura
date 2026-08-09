package com.aura.companion.screen

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * §21: screen observation filtering.
 *
 * [ObservationThrottle] is the gate that decides whether a screen becomes
 * a request at all, so its failure modes are expensive in both directions:
 * too loose and it drains a battery on a metered connection, too tight and
 * Aura never sees the screen the user wanted help with.
 *
 * The clock is injected, so none of this sleeps.
 */
class ObservationThrottleTest {

    /**
     * A realistic epoch, not zero.
     *
     * The throttle compares `now - lastSentMs` against the interval, with
     * `lastSentMs` starting at 0 to mean "never sent". Against a real
     * millisecond clock that difference is decades and the first screen
     * passes; a test clock starting at 0 would make it *look* as though
     * the first screen is throttled, which is an artefact of the fake and
     * not the behaviour on a phone.
     */
    private var now = 1_700_000_000_000L

    private fun throttle(
        minIntervalMs: Long = ObservationThrottle.DEFAULT_MIN_INTERVAL_MS,
        similarity: Double = ObservationThrottle.DEFAULT_SIMILARITY,
    ) = ObservationThrottle(
        minIntervalMs = minIntervalMs,
        similarityThreshold = similarity,
        clock = { now },
    )

    // ------------------------------------------------------------------
    // Rate limiting
    // ------------------------------------------------------------------

    @Test
    fun `the first screen is always sent`() {
        assertTrue(throttle().accept("com.example.app", "a page about gardening"))
    }

    @Test
    fun `a different screen inside the interval is still refused`() {

        val throttle = throttle()

        assertTrue(throttle.accept("com.example.app", "a page about gardening"))

        now += 1_000

        // Different content, but too soon. The interval is a floor on
        // request rate, not only a duplicate filter.
        assertFalse(throttle.accept("com.example.app", "an article on marine biology"))
    }

    @Test
    fun `a different screen after the interval is sent`() {

        val throttle = throttle()

        throttle.accept("com.example.app", "a page about gardening")

        now += ObservationThrottle.DEFAULT_MIN_INTERVAL_MS

        assertTrue(throttle.accept("com.example.app", "an article on marine biology"))
    }

    @Test
    fun `an unchanged screen never pushes the baseline forward`() {

        val throttle = throttle()

        throttle.accept("com.example.app", "the quick brown fox jumps over the lazy dog")

        // Idle on the same screen for a long time.
        now += ObservationThrottle.DEFAULT_MIN_INTERVAL_MS * 5
        assertFalse(
            throttle.accept("com.example.app", "the quick brown fox jumps over the lazy dog")
        )

        // Then it changes. If rejecting the duplicate had reset the clock,
        // this would be refused - and a slowly changing screen would never
        // be observed at all.
        assertTrue(
            throttle.accept("com.example.app", "an entirely different subject entirely")
        )
    }

    // ------------------------------------------------------------------
    // Change detection
    // ------------------------------------------------------------------

    @Test
    fun `the same screen is not sent twice`() {

        val throttle = throttle()

        val text = "inbox unread messages from alice and bob about the quarterly report"

        assertTrue(throttle.accept("com.example.mail", text))

        now += ObservationThrottle.DEFAULT_MIN_INTERVAL_MS * 2

        assertFalse(throttle.accept("com.example.mail", text))
    }

    @Test
    fun `a ticking clock is not a change`() {

        val throttle = throttle()

        assertTrue(
            throttle.accept(
                "com.example.reader",
                "10:04 chapter seven the lighthouse keeper watched the harbour",
            )
        )

        now += ObservationThrottle.DEFAULT_MIN_INTERVAL_MS * 2

        // One minute later, same page. Splitting on ':' reduces the clock
        // to fragments the length filter drops, so the two screens are the
        // same screen.
        assertFalse(
            throttle.accept(
                "com.example.reader",
                "10:05 chapter seven the lighthouse keeper watched the harbour",
            )
        )
    }

    @Test
    fun `a counter ticking is not a change`() {

        val throttle = throttle()

        assertTrue(
            throttle.accept(
                "com.example.mail",
                "inbox 1284 unread messages from alice about the quarterly report",
            )
        )

        now += ObservationThrottle.DEFAULT_MIN_INTERVAL_MS * 2

        // A badge going 1284 -> 1285. Collapsed to a placeholder, matching
        // companion/detector.py, so the request is never made rather than
        // being made and discarded server-side.
        //
        // Four digits on purpose: a single-digit badge is removed by the
        // length filter anyway, so a one-character counter would pass this
        // test even with the volatile rule deleted. At this length the two
        // screens differ by one token in eleven - similarity 0.82, under
        // the 0.85 threshold - and only the placeholder makes them equal.
        assertFalse(
            throttle.accept(
                "com.example.mail",
                "inbox 1285 unread messages from alice about the quarterly report",
            )
        )
    }

    @Test
    fun `a download percentage ticking is not a change`() {

        val throttle = throttle()

        assertTrue(
            throttle.accept(
                "com.example.browser",
                "downloading installer package 45% complete 350ms remaining estimate",
            )
        )

        now += ObservationThrottle.DEFAULT_MIN_INTERVAL_MS * 2

        assertFalse(
            throttle.accept(
                "com.example.browser",
                "downloading installer package 82% complete 120ms remaining estimate",
            )
        )
    }

    @Test
    fun `a token that merely contains digits keeps its identity`() {

        val throttle = throttle()

        // If `abc123` collapsed to the counter placeholder, two genuinely
        // different screens full of identifiers would look identical.
        assertTrue(throttle.accept("com.example.ide", "error at handler7 in module alpha3"))

        now += ObservationThrottle.DEFAULT_MIN_INTERVAL_MS * 2

        assertTrue(throttle.accept("com.example.ide", "error at handler9 in module beta4"))
    }

    @Test
    fun `real new content is sent`() {

        val throttle = throttle()

        throttle.accept("com.example.reader", "chapter one the lighthouse keeper woke early")

        now += ObservationThrottle.DEFAULT_MIN_INTERVAL_MS

        assertTrue(
            throttle.accept(
                "com.example.reader",
                "chapter two the storm arrived without warning that evening",
            )
        )
    }

    @Test
    fun `switching app is always interesting however similar the text`() {

        val throttle = throttle()

        val text = "compose a message to alice about the quarterly report"

        throttle.accept("com.example.mail", text)

        now += ObservationThrottle.DEFAULT_MIN_INTERVAL_MS

        // Identical text, different app. Where the user is doing something
        // is context in its own right.
        assertTrue(throttle.accept("com.example.chat", text))
    }

    @Test
    fun `an empty screen is never sent`() {

        val throttle = throttle()

        assertFalse(throttle.accept("com.example.app", ""))
        assertFalse(throttle.accept("com.example.app", "   \n\t  "))

        // Nor does a rejected empty screen consume the interval.
        assertTrue(throttle.accept("com.example.app", "actual readable content here"))
    }

    @Test
    fun `a screen of nothing but short words is not sent`() {

        // Every token below the length filter, so there is nothing to
        // compare and nothing worth a request.
        assertFalse(throttle().accept("com.example.app", "a b c to of in on at"))
    }

    // ------------------------------------------------------------------
    // The cheap pre-check
    // ------------------------------------------------------------------

    @Test
    fun `a burst of events costs one tree walk`() {

        val throttle = throttle()

        assertTrue(throttle.allowsAttempt())

        // Android delivers content-changed events far faster than this.
        repeat(20) {
            now += 50
            assertFalse(throttle.allowsAttempt())
        }
    }

    @Test
    fun `allowsAttempt refuses while the send interval is still running`() {

        val throttle = throttle()

        throttle.allowsAttempt()
        throttle.accept("com.example.app", "some readable screen content")

        now += ObservationThrottle.ATTEMPT_INTERVAL_MS * 2

        // Past the walk interval but not the send interval: walking the
        // tree now would produce text with nowhere to go.
        assertFalse(throttle.allowsAttempt())

        now += ObservationThrottle.DEFAULT_MIN_INTERVAL_MS

        assertTrue(throttle.allowsAttempt())
    }

    @Test
    fun `reset forgets the previous screen entirely`() {

        val throttle = throttle()

        val text = "inbox unread messages from alice about the quarterly report"

        throttle.accept("com.example.mail", text)

        // What happens when the user switches observation off and on
        // again: the next screen is new, whatever it says.
        throttle.reset()

        assertTrue(throttle.accept("com.example.mail", text))
    }
}
