package com.aura.companion.ui.hub

import com.aura.companion.data.AuraError
import com.aura.companion.ui.components.StatusTone

/**
 * Why the hub can or cannot configure this server.
 *
 * THE BUG THIS EXISTS TO FIX
 * -------------------------
 * The settings verdict used to be a boolean (`ServerState.loaded`) plus a
 * free-text sentence, and five separate places re-derived their own wording
 * from the boolean alone. Once `GET /api/health` had returned 200, *every*
 * subsequent settings failure - a refused token, a rate limit, a 500 from the
 * settings service, a read timeout, a payload this build cannot parse - was
 * rendered as the single sentence "This Aura server does not expose settings".
 *
 * On a free-tier host that is reachable with no server bug at all: health
 * succeeds, the very next request meets a cold-start gateway error, and the
 * app blames the server for missing a feature it has. The user is then told to
 * update a server that is already current, about an endpoint that answers.
 *
 * So the state carries *why*, once, here. Each member is a distinct thing that
 * happened, with the sentence for it, and "does not expose settings" is
 * reachable from exactly one of them: [NotExposed], which is 404/405 and
 * nothing else.
 *
 * WHY AN ENUM WITH STRINGS ON IT
 * ------------------------------
 * This module's unit tests are plain JVM - no Robolectric, no Compose test
 * harness - so wording that lives inside a `@Composable` cannot be asserted.
 * Every consumer ([HubUiState.lockedReason], [hubHeadline], [hubBanner],
 * `AuraSection`, `ConnectionSection`, `DiagnosticsSection`) reads these
 * properties instead of composing its own sentence, which is what stops the
 * six of them drifting apart again.
 *
 * @property label two or three words, for a status row's value
 * @property reason one sentence, no trailing full stop: why a control is
 *   locked. Rendered next to rows and as a subtitle, so it is phrased as the
 *   answer to "why can't I change this".
 * @property headline the second line under "Connected" on the front page.
 *   Blank where the state is not reachable with a live server.
 * @property retryable whether asking again could plausibly succeed without
 *   anyone changing anything. Drives whether the banner says "pull to
 *   refresh" - offering that for a 404 would be a lie.
 */
enum class SettingsAccess(
    val label: String,
    val reason: String,
    val headline: String,
    val tone: StatusTone,
    val retryable: Boolean = false,
) {

    /** `GET /api/settings` returned 200 and it parsed. Nothing is locked. */
    Available(
        label = "Available",
        reason = "",
        headline = "",
        tone = StatusTone.Good,
    ),

    /** Asked, not answered yet. Honest for the window between the two calls. */
    Loading(
        label = "Loading",
        reason = "Waiting for Aura's settings",
        headline = "Chat works. Reading Aura's settings",
        tone = StatusTone.Neutral,
        retryable = true,
    ),

    /** Never asked: no server address, or nothing answered. */
    NotConnected(
        label = "Not reached",
        reason = "Connect to Aura to change this",
        headline = "",
        tone = StatusTone.Neutral,
    ),

    /**
     * 404 or 405. The **only** state allowed to say this.
     *
     * A deployment older than the app answers `/api/chat` and `/api/health`
     * perfectly and has no hub API at all, which is a real state and keeps its
     * real sentence.
     */
    NotExposed(
        label = "Not on this server",
        reason = "This Aura server does not expose settings",
        headline = "Chat works. Settings unavailable on this server.",
        tone = StatusTone.Warning,
    ),

    /** 401 on settings specifically, though health accepted the same token. */
    AuthRequired(
        label = "Token refused",
        reason = "Aura refused this token for settings",
        headline = "Chat works. Aura refused this token for settings.",
        tone = StatusTone.Bad,
    ),

    /** 403: the token is recognised and not permitted here. */
    Forbidden(
        label = "Not permitted",
        reason = "This token is not allowed to read Aura's settings",
        headline = "Chat works. This token may not read settings.",
        tone = StatusTone.Bad,
    ),

    /** 422. Aura read the request and refused it, in its own words. */
    Refused(
        label = "Refused",
        reason = "Aura would not accept the settings request",
        headline = "Chat works. Aura refused the settings request.",
        tone = StatusTone.Warning,
    ),

    /** 429. Nothing is broken; waiting is the whole fix. */
    RateLimited(
        label = "Rate limited",
        reason = "Aura is receiving too many requests. Try again shortly",
        headline = "Chat works. Settings are rate limited right now.",
        tone = StatusTone.Warning,
        retryable = true,
    ),

    /** 5xx from the settings service itself. The server, not the feature. */
    ServerError(
        label = "Server error",
        reason = "Aura's settings service hit an error. Try again shortly",
        headline = "Chat works. Aura's settings service is failing.",
        tone = StatusTone.Bad,
        retryable = true,
    ),

    /** A gateway error or a 503 with no Aura behind it yet. */
    Waking(
        label = "Starting up",
        reason = "Aura is still starting up. Try again in a moment",
        headline = "Chat works. Aura is still starting up.",
        tone = StatusTone.Neutral,
        retryable = true,
    ),

    /** The request itself did not complete: timeout, or no route. */
    Network(
        label = "No answer",
        reason = "Aura's settings did not load over this connection",
        headline = "Chat works. Settings did not load over this connection.",
        tone = StatusTone.Warning,
        retryable = true,
    ),

    /**
     * The route answered successfully and this build cannot read the answer.
     *
     * Never [NotExposed]: the endpoint exists. Saying "does not expose
     * settings" about a 200 is precisely the false claim this file exists to
     * prevent, and this is the state that used to be swallowed into it.
     */
    Incompatible(
        label = "Incompatible",
        reason = "This app could not read Aura's settings. " +
            "The app or the server may need updating",
        headline = "Chat works. Aura's settings are in a form this app " +
            "cannot read.",
        tone = StatusTone.Bad,
    ),

    /** Something failed and named no cause. Says exactly that, and no more. */
    Unexplained(
        label = "Unavailable",
        reason = "Aura did not return its settings",
        headline = "Chat works. Aura did not return its settings.",
        tone = StatusTone.Warning,
        retryable = true,
    );

    /** True when the hub has a document to render and PATCH. */
    val usable: Boolean get() = this == Available
}

/**
 * The settings verdict, from the three facts the ViewModel observes.
 *
 * @param loaded whether a settings document is in hand
 * @param connected whether `GET /api/health` returned 200 - the server is Aura
 *   and took this token
 * @param error the typed failure from the settings request, or null if it has
 *   not failed (not asked yet, in flight, or succeeded)
 */
fun settingsAccess(
    loaded: Boolean,
    connected: Boolean,
    error: AuraError?,
): SettingsAccess = when {

    loaded -> SettingsAccess.Available

    // No failure recorded. Either nothing has been asked, or the failure that
    // matters is the connection itself - and repeating "could not reach Aura"
    // beside every row is noise the status line already carries.
    error == null ->
        if (connected) SettingsAccess.Loading else SettingsAccess.NotConnected

    else -> when (error) {
        is AuraError.NotSupported -> SettingsAccess.NotExposed
        is AuraError.Unauthorized -> SettingsAccess.AuthRequired
        is AuraError.Forbidden -> SettingsAccess.Forbidden
        is AuraError.Rejected -> SettingsAccess.Refused
        is AuraError.RateLimited -> SettingsAccess.RateLimited
        is AuraError.Incompatible -> SettingsAccess.Incompatible
        is AuraError.Waking -> SettingsAccess.Waking
        is AuraError.Timeout, is AuraError.Offline -> SettingsAccess.Network
        is AuraError.ServerFailure -> SettingsAccess.ServerError
        // A subsystem switched off server-side, which the settings route does
        // not do - but a 503 carrying Aura's own marker is still Aura saying
        // "not now" rather than "not here".
        is AuraError.Unavailable -> SettingsAccess.ServerError
        is AuraError.NotConfigured -> SettingsAccess.NotConnected
        is AuraError.Unknown -> SettingsAccess.Unexplained
    }
}

/**
 * The banner sentence for a settings failure, or null when there is none.
 *
 * One sentence explaining the state, one saying what it costs, and - only
 * where it is true - one saying what to do. The "until the server is updated"
 * tail belongs to [SettingsAccess.NotExposed] alone: it is advice about a
 * server that is genuinely behind this app, and it is wrong for every other
 * state.
 */
fun settingsBanner(access: SettingsAccess): String? = when (access) {

    SettingsAccess.Available,
    SettingsAccess.NotConnected,
    SettingsAccess.Loading,
    -> null

    SettingsAccess.NotExposed -> access.reason +
        ". The sections below are read-only until the server is updated. " +
        "Chat is unaffected."

    else -> buildString {
        append(access.reason)
        append(". The sections below are read-only until Aura's settings load.")
        if (access.retryable) append(" Pull down to try again.")
        append(" Chat is unaffected.")
    }
}

/**
 * The paragraph Diagnostics shows under the status rows, or null.
 *
 * Longer than [settingsBanner] because Diagnostics is where someone goes to
 * find out what to *do*. The advice differs per state, which is the point: the
 * old text told everyone to update their deployment, including the people whose
 * deployment was current and whose request had merely been rate limited.
 */
fun settingsNotice(access: SettingsAccess): String? = when (access) {

    SettingsAccess.Available,
    SettingsAccess.NotConnected,
    SettingsAccess.Loading,
    -> null

    SettingsAccess.NotExposed ->
        "This Aura is running and answering chat, but its build does not " +
            "have the settings API this app uses. Update the deployment to " +
            "configure it from the phone. Nothing is wrong with the " +
            "connection or the token."

    SettingsAccess.AuthRequired, SettingsAccess.Forbidden ->
        access.reason + ". The health check accepted this same token, so " +
            "the restriction is on the settings route rather than on the " +
            "token as a whole."

    SettingsAccess.Incompatible ->
        access.reason + ". The route answered - this build could not read " +
            "what it said, which is a version mismatch rather than a " +
            "missing feature."

    else -> buildString {
        append(access.reason)
        append(". The connection and the token are fine; this one request ")
        append("did not come back.")
        if (access.retryable) append(" Pull down to try again.")
    }
}
