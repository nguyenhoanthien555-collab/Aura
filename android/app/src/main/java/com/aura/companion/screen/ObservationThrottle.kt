package com.aura.companion.screen

/**
 * Decides whether a screen is worth a request.
 *
 * The server has its own change detection and cooldown - this is not a
 * duplicate of it. The two guard different resources: the server's gates
 * protect the *user's attention*, this one protects the *phone's battery
 * and data plan*. A screen rejected here never becomes a request at all,
 * which is the only saving that matters on a metered connection.
 *
 * Not thread-safe by design. It is only ever touched from the
 * accessibility callback, which Android delivers on the main thread.
 */
class ObservationThrottle(
    private val minIntervalMs: Long = DEFAULT_MIN_INTERVAL_MS,
    private val similarityThreshold: Double = DEFAULT_SIMILARITY,
    private val clock: () -> Long = System::currentTimeMillis,
) {

    private var lastAttemptMs = 0L
    private var lastSentMs = 0L
    private var lastPackage = ""
    private var lastTokens: Set<String> = emptySet()

    /**
     * A cheap pre-check, before the accessibility tree is walked.
     *
     * Walking the tree is the expensive part, so the clock is consulted
     * first. Rate-limited harder than the send interval so a burst of
     * content-changed events costs almost nothing.
     */
    fun allowsAttempt(): Boolean {

        val now = clock()

        if (now - lastAttemptMs < ATTEMPT_INTERVAL_MS) return false

        lastAttemptMs = now

        return now - lastSentMs >= minIntervalMs
    }

    /**
     * Should this screen be sent?
     *
     * Records the screen as sent when it returns true, so a caller that
     * ignores the answer will still be throttled correctly on the next
     * call.
     */
    fun accept(packageName: String, text: String): Boolean {

        val now = clock()

        if (now - lastSentMs < minIntervalMs) return false

        val tokens = tokenise(text)

        if (tokens.isEmpty()) return false

        // A new app is always interesting, however similar its text.
        val switched = packageName != lastPackage

        if (!switched && similarity(tokens, lastTokens) >= similarityThreshold) {
            // Same screen. Refresh the baseline timestamp? No - deliberately
            // not. Letting an unchanging screen push the clock forward would
            // mean a screen that changes slowly never crosses the interval.
            return false
        }

        lastSentMs = now
        lastPackage = packageName
        lastTokens = tokens

        return true
    }

    fun reset() {
        lastAttemptMs = 0
        lastSentMs = 0
        lastPackage = ""
        lastTokens = emptySet()
    }

    /**
     * Jaccard overlap of the two token sets.
     *
     * The same measure the server uses, for the same reason: a clock
     * ticking in a status bar changes one token out of hundreds, and any
     * measure sensitive to that would fire once a second forever.
     */
    private fun similarity(a: Set<String>, b: Set<String>): Double {

        if (a.isEmpty() && b.isEmpty()) return 1.0
        if (a.isEmpty() || b.isEmpty()) return 0.0

        val intersection = a.count { it in b }.toDouble()
        val union = (a.size + b.size - intersection)

        return if (union <= 0) 0.0 else intersection / union
    }

    /**
     * The words on a screen, with self-changing ones neutralised.
     *
     * This mirrors one specific thing the server does in
     * `companion/detector.py`: collapsing tokens that change by themselves
     * - counters, percentages, sizes, the numeric parts of a date - to a
     * single placeholder, so a badge going from 3 to 4 is not mistaken for
     * new content.
     *
     * It is deliberately the *only* thing mirrored. Relevance, cooldown
     * and the decision to say anything stay on the server, and duplicating
     * those here would mean two implementations to keep in agreement. This
     * one is copied because the alternative is worse than duplication: a
     * screen the server is certain to discard as unchanged would otherwise
     * still cost a radio wake-up and a request on a metered connection.
     *
     * Clocks need no rule of their own here. Splitting on `:` already
     * breaks `10:04` into two-character fragments that the length filter
     * removes, which reaches the server's conclusion - a ticking clock is
     * not a change - by a different route.
     */
    private fun tokenise(text: String): Set<String> =
        text.lowercase()
            .split(*DELIMITERS)
            .asSequence()
            .map { it.trim() }
            .filter { it.length > 2 }
            .map { if (VOLATILE.matches(it)) VOLATILE_PLACEHOLDER else it }
            .take(MAX_TOKENS)
            .toSet()

    companion object {
        /** Minimum gap between two observations sent to the server. */
        const val DEFAULT_MIN_INTERVAL_MS = 12_000L

        /** Above this overlap, two screens count as the same screen. */
        const val DEFAULT_SIMILARITY = 0.85

        /** Minimum gap between two tree walks. */
        const val ATTEMPT_INTERVAL_MS = 1_500L

        private const val MAX_TOKENS = 400

        /**
         * A number, on its own or with a unit: `1024`, `45%`, `350ms`,
         * `1.5gb`, `+12`. Anchored, so a token that merely contains digits
         * keeps its identity - `abc123` is a name, not a counter.
         *
         * Kept in step with `_VOLATILE` in `companion/detector.py`.
         */
        private val VOLATILE =
            Regex("""[+-]?\d[\d,._]*(%|s|ms|kb|mb|gb|tb|k|m|b)?""")

        private const val VOLATILE_PLACEHOLDER = "<count>"

        private val DELIMITERS = charArrayOf(
            ' ', '\n', '\t', '\r', ',', '.', ':', ';', '!', '?',
            '(', ')', '[', ']', '{', '}', '"', '\'', '/', '\\', '|', '-',
        )
    }
}
