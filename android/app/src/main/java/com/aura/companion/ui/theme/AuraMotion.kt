package com.aura.companion.ui.theme

/**
 * How long things take to move.
 *
 * Three durations, not thirty. A companion app that animates every surface
 * at its own speed reads as unfinished rather than as alive, and a shared
 * scale is what makes a transition feel like the same app moving.
 *
 *   Quick     a state change on something already on screen - a toggle
 *             settling, a colour crossing from amber to green
 *   Standard  something arriving or leaving: a notice, an expanded card
 *   Slow      a status ring breathing, where the point is that it is
 *             *barely* noticeable
 *
 * This file imports nothing on purpose. The durations are decisions, and
 * [scaled] is one that must hold under test - see `AuraMotionTest` - so
 * neither may sit behind a `@Composable`.
 */
object AuraMotion {

    const val Quick = 140
    const val Standard = 240
    const val Slow = 420

    /**
     * A duration honouring the system's animation scale.
     *
     * Returns 0 - an instant change, not a slow one - when the user has
     * turned animations off. That setting is an accessibility request from
     * someone for whom motion is a problem, or a battery decision on a
     * phone that is nearly flat; halving the duration would answer neither.
     *
     * 0 is safe to pass to `tween`, which treats it as "already finished".
     */
    fun scaled(base: Int, reduced: Boolean): Int = if (reduced) 0 else base

    /**
     * Whether a continuously repeating animation may run at all.
     *
     * Repeating animations are the ones with a real cost: a status ring that
     * breathes forever keeps the frame pipeline awake for as long as the
     * screen is on. Aura runs them only while something is genuinely in
     * flight, and never against a reduced-motion request.
     */
    fun mayLoop(reduced: Boolean, busy: Boolean): Boolean = !reduced && busy
}
