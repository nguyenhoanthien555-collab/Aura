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

    /** Reachable, but refused the token. */
    data object Unauthorized : AuraError {
        override val userMessage = "Aura rejected the access token. Check it in Settings."
    }

    /** The feature is switched off server-side. */
    data class Unavailable(val detail: String) : AuraError {
        override val userMessage = detail
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
            413 -> "That was too large to send."
            422 -> "Aura could not read that message."
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
