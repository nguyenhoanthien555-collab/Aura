package com.aura.companion.data

/**
 * What went wrong, in terms the UI can act on.
 *
 * Not an exception type per failure - a sealed hierarchy, because the
 * screen has to *render* the difference. "Re-enter your token" and "the
 * server is waking up, hold on" lead the user to opposite actions, and
 * collapsing both into "Error" is how a working configuration gets
 * changed for no reason.
 */
sealed interface AuraError {

    val userMessage: String

    /** No server URL configured yet. First run. */
    data object NotConfigured : AuraError {
        override val userMessage = "Set your Aura server address to get started."
    }

    /** No route to the host: airplane mode, no signal, wrong LAN. */
    data object Offline : AuraError {
        override val userMessage = "No connection. Check your network."
    }

    /**
     * The server did not answer in time.
     *
     * On a free tier this is usually a suspended service waking up, which
     * is why the wording suggests waiting rather than reconfiguring.
     */
    data object Timeout : AuraError {
        override val userMessage = "Aura is taking a while to answer. It may be waking up."
    }

    /**
     * The host answered, but Aura is not up behind it yet.
     *
     * Distinct from [Timeout] because it is a *positive* signal rather
     * than an absence of one: a free-tier platform returns a gateway error
     * from its own edge while it starts the container. Waiting is the
     * right response and reconfiguring is not, so it must not be reported
     * as a failure the user has to act on.
     */
    data object Waking : AuraError {
        override val userMessage = "Aura server is waking up. Try again in a moment."
    }

    /** Reachable, but refused the token. HTTP 401. */
    data object Unauthorized : AuraError {
        override val userMessage = "Aura rejected the access token. Check it in Settings."
    }

    /**
     * The token was recognised and this request was still refused. HTTP 403.
     *
     * Split from [Unauthorized] because the two lead to opposite actions: a
     * 401 means "the token is wrong, replace it", a 403 means "the token is
     * right and does not carry this permission", and telling someone to
     * re-enter a working token is how a working configuration gets broken.
     * Aura's own routes answer 401 (`verify_token`), so a 403 comes from
     * something in front of them - a proxy, or a platform access rule.
     */
    data object Forbidden : AuraError {
        override val userMessage =
            "Aura accepted the token but refused this request."
    }

    /**
     * Too many requests, too quickly. HTTP 429.
     *
     * Its own case rather than a [ServerFailure] code, because nothing is
     * broken and nothing needs configuring: the only correct response is to
     * wait. Reported as a server failure it looked like a fault, and the hub
     * then blamed the *feature* for being absent.
     */
    data object RateLimited : AuraError {
        override val userMessage = "Aura is receiving too many requests. Try again shortly."
    }

    /**
     * The request succeeded and this app could not read the answer.
     *
     * A 200 whose body will not deserialize - a truncated response, a proxy's
     * HTML error page served with a success code, or a server document whose
     * shape this build predates. Emphatically **not** [NotSupported]: the
     * route exists and answered. Conflating the two is what made the hub
     * claim "this server does not expose settings" about a server that had
     * just returned its settings, so the distinction is the whole point of
     * this case existing.
     *
     * The detail is a category, never the parse exception: a serialization
     * message quotes the offending JSON, which can contain configuration.
     */
    data class Incompatible(val detail: String = "") : AuraError {
        override val userMessage =
            "Aura's reply was not in a form this app can read. " +
                "The app or the server may need updating."
    }

    /** The feature is switched off server-side. */
    data class Unavailable(val detail: String) : AuraError {
        override val userMessage = detail
    }

    /**
     * The route is not on this server.
     *
     * A 404 from an endpoint this app knows by name means the server is
     * *older* than the app, not that it is down. The Control Hub API
     * arrived after chat did, so a deployment from before it answers
     * `/api/chat` and `/api/health` perfectly while 404-ing
     * `/api/settings`. Reporting that as a dead server is how a working
     * configuration gets torn up for no reason, so it is its own case -
     * the hub branches on it to say "connected, settings unavailable"
     * rather than "disconnected".
     */
    data object NotSupported : AuraError {
        override val userMessage =
            "This Aura server does not have this feature yet. It may need updating."
    }

    /**
     * The server validated the request and refused it.
     *
     * Distinct from [ServerFailure] because the server knows *why* and has
     * said so in words meant for this screen - "proactive.max_per_day must
     * be between 1 and 20". Those messages come from
     * `core/settings_store.py`, which is written to name the offending
     * setting and to contain no exception text, so passing one through is
     * both safe and far more useful than "unexpected response (422)".
     *
     * Carries the implication that nothing changed: settings PATCHes are
     * all-or-nothing on the server.
     */
    data class Rejected(val detail: String) : AuraError {
        override val userMessage =
            detail.ifBlank { "Aura would not accept that setting." }
    }

    /** The server failed. The detail is a code, never an exception. */
    data class ServerFailure(val code: Int) : AuraError {
        override val userMessage = when (code) {
            in 500..599 -> "Aura hit an error. Try again in a moment."
            400 -> "Aura could not read that request."
            409 -> "Another change is already in progress. Try again."
            413 -> "That was too large to send."
            422 -> "Aura could not read that message."
            429 -> "Too many requests. Give Aura a moment."
            else -> "Aura returned an unexpected response ($code)."
        }
    }

    /** Anything else. */
    data class Unknown(val detail: String = "") : AuraError {
        override val userMessage = "Something went wrong reaching Aura."
    }
}

/**
 * Success or a typed failure.
 *
 * Deliberately not `kotlin.Result`: that carries a `Throwable`, and a
 * throwable is exactly what must not reach the UI. A network exception
 * message can name an internal host or a path.
 */
sealed interface AuraResult<out T> {
    data class Ok<T>(val value: T) : AuraResult<T>
    data class Failed(val error: AuraError) : AuraResult<Nothing>
}

inline fun <T, R> AuraResult<T>.map(transform: (T) -> R): AuraResult<R> = when (this) {
    is AuraResult.Ok -> AuraResult.Ok(transform(value))
    is AuraResult.Failed -> this
}

fun <T> AuraResult<T>.getOrNull(): T? = (this as? AuraResult.Ok)?.value
