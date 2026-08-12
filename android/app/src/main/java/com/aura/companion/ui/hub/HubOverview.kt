package com.aura.companion.ui.hub

import com.aura.companion.ui.components.StatusTone

/**
 * What the hub says at a glance, decided in plain Kotlin.
 *
 * WHY THIS IS NOT IN THE COMPOSABLE
 * --------------------------------
 * It was: a chain of `when` blocks inside `StatusCard`, which is the one
 * place in the app where getting the wording wrong is most visible and the
 * hardest to test. This module's Compose test harness is
 * `androidTestImplementation` only - no Robolectric, no JVM Compose - so a
 * verdict that lives inside a `@Composable` cannot be asserted at all. Here
 * it can, and `HubOverviewTest` does.
 *
 * WHAT THE WORDING HAS TO GET RIGHT
 * ---------------------------------
 * A server that answers `/api/health` and 404s `/api/settings` is
 * **connected**. The app used to call that "Disconnected" and send the user
 * to re-enter a token that was never wrong, while chat worked in the next
 * tab. So the headline reads reachability from the health rung and treats a
 * missing settings API as a second line, never as a different verdict - and
 * no string here says "unexpected response" when a real explanation exists.
 */

/** The one-line verdict at the top of the hub. */
data class HubHeadline(
    val title: String,
    val detail: String,
    val tone: StatusTone,
    /**
     * Whether something is genuinely in flight.
     *
     * Drives the status ring's animation, and only ever while true - a ring
     * that breathes forever keeps the frame pipeline awake for as long as
     * the screen is on, on a phone, for decoration.
     */
    val busy: Boolean = false,
)

/**
 * The headline, from the reachability ladder and the provider chain.
 *
 * Order matters and is not arbitrary: the fallback case has to be tested
 * before the plain connected case or a substitute provider would report as
 * unqualified success, and `loading` has to come after both so a background
 * refresh does not blank out a state that is already known.
 */
fun hubHeadline(state: HubUiState): HubHeadline {

    val server = state.server

    val provider = state.activeProviderLabel

    return when {

        state.connected && server.health.inFallback -> HubHeadline(
            title = "Running on a fallback",
            detail = if (provider.isBlank()) {
                "The chosen provider failed; a substitute is answering"
            } else {
                "$provider is answering instead of the chosen provider"
            },
            tone = StatusTone.Warning,
        )

        // Reachable and authenticated, but this build of the app knows
        // endpoints that deployment does not. Say which half works, because
        // both halves are true and only one of them is a problem.
        state.connected && server.settingsProblem != null -> HubHeadline(
            title = "Connected",
            detail = "Chat works. Settings unavailable on this server.",
            tone = StatusTone.Warning,
        )

        state.connected -> HubHeadline(
            title = "Connected",
            detail = if (provider.isBlank()) {
                "Aura is ready"
            } else {
                "$provider is answering"
            },
            tone = StatusTone.Good,
        )

        state.loading -> HubHeadline(
            title = "Connecting…",
            detail = "Asking Aura how it is",
            tone = StatusTone.Neutral,
            busy = true,
        )

        // Something answered on that address and refused the token.
        // `/api/health` is itself behind the token, so this is the only
        // thing a 401 there can mean.
        server.reach == ServerReach.Connected -> HubHeadline(
            title = "Server reachable",
            detail = state.error ?: "That address answered, but not with this token",
            tone = StatusTone.Bad,
        )

        !state.device.isConfigured -> HubHeadline(
            title = "Not set up",
            detail = "Enter your server address to connect",
            tone = StatusTone.Neutral,
        )

        else -> HubHeadline(
            title = "Disconnected",
            detail = state.error ?: "Could not reach Aura at that address",
            tone = StatusTone.Bad,
        )
    }
}

/**
 * The provider actually answering, by its display label.
 *
 * Prefers the active provider over the requested one, because during a
 * fallback those differ and the active one is the answer to "who is talking
 * to me". Falls back to the raw name when this build has no label for it,
 * so a server newer than the app still names its provider.
 */
val HubUiState.activeProviderLabel: String
    get() {

        val name = server.health.active.ifBlank { server.health.requested }
            .ifBlank { server.config.llm.provider }

        if (name.isBlank()) return ""

        return server.providers.firstOrNull { it.name == name }
            ?.label?.takeIf { it.isNotBlank() }
            ?: name
    }

// ----------------------------------------------------------------------
// The compact status tiles
// ----------------------------------------------------------------------

/** Which tile this is, so the screen can pick an icon without a string match. */
enum class HubTileKind { Provider, Memory, Awareness, Proactive }

/**
 * One at-a-glance tile.
 *
 * [value] is two or three words. A tile that needs a sentence is a section,
 * and there is a row below for those.
 */
data class HubTile(
    val kind: HubTileKind,
    val label: String,
    val value: String,
    val tone: StatusTone,
    /** The hub route this tile opens, so a glance can become a change. */
    val route: String,
)

/**
 * The four tiles under the hero card.
 *
 * Chosen as the four things a user checks without meaning to open anything:
 * who is answering, whether Aura is remembering, whether it can see the
 * screen, and whether it may speak first. The last two are the invasive
 * ones, which is exactly why they are on the front page rather than three
 * taps down - a capability that is on should be visible without hunting.
 *
 * Unknown reads as unknown. Before the settings document arrives every value
 * here is "—" with a neutral tone, because claiming "Off" for a setting
 * nobody has reported yet would be a guess about someone's privacy.
 */
fun hubTiles(state: HubUiState): List<HubTile> {

    val known = state.settingsAvailable

    val config = state.server.config

    return listOf(
        HubTile(
            kind = HubTileKind.Provider,
            label = "Provider",
            value = state.activeProviderLabel.ifBlank { "—" },
            tone = when {
                !state.connected -> StatusTone.Neutral
                state.server.health.inFallback -> StatusTone.Warning
                state.server.health.ready -> StatusTone.Good
                else -> StatusTone.Neutral
            },
            route = HubRoutes.MODELS,
        ),
        HubTile(
            kind = HubTileKind.Memory,
            label = "Memory",
            value = when {
                !known -> "—"
                config.memory.recall -> "Recall on"
                config.memory.pipeline || config.memory.profile -> "Remembering"
                else -> "Off"
            },
            tone = when {
                !known -> StatusTone.Neutral
                config.memory.pipeline || config.memory.profile -> StatusTone.Good
                else -> StatusTone.Neutral
            },
            route = HubRoutes.MEMORY,
        ),
        HubTile(
            kind = HubTileKind.Awareness,
            label = "Awareness",
            // Two switches, one answer. The phone's own toggle is what
            // decides whether anything is captured at all, so it is named
            // first when the two disagree - "the server is willing" is not
            // a state anybody needs to act on.
            value = when {
                !known -> if (state.device.screenObservationEnabled) "Phone on" else "—"
                state.device.screenObservationEnabled && config.server.screen.enabled ->
                    "Watching"
                state.device.screenObservationEnabled -> "Server off"
                config.server.screen.enabled -> "Phone off"
                else -> "Off"
            },
            tone = when {
                !known -> StatusTone.Neutral
                state.device.screenObservationEnabled && config.server.screen.enabled ->
                    StatusTone.Warning
                state.device.screenObservationEnabled -> StatusTone.Warning
                else -> StatusTone.Neutral
            },
            route = HubRoutes.AWARENESS,
        ),
        HubTile(
            kind = HubTileKind.Proactive,
            label = "Proactive",
            value = when {
                !known -> "—"
                config.proactive.enabled -> "On"
                else -> "Off"
            },
            tone = when {
                !known -> StatusTone.Neutral
                config.proactive.enabled -> StatusTone.Good
                else -> StatusTone.Neutral
            },
            route = HubRoutes.PROACTIVE,
        ),
    )
}

// ----------------------------------------------------------------------
// The notice under the hero card
// ----------------------------------------------------------------------

/** A banner and its tone, or null when there is nothing worth saying. */
data class HubBanner(val text: String, val tone: StatusTone)

/**
 * The one banner the hub shows, if any.
 *
 * One, not a stack: three warnings at the top of a screen is a screen nobody
 * reads. Ordered by what the user can do about it - an unconfigured app
 * first, because nothing else on this screen can work until that is fixed.
 */
fun hubBanner(state: HubUiState): HubBanner? {

    val server = state.server

    return when {

        !state.device.isConfigured -> HubBanner(
            text = "No server connected. Set your server address and token in " +
                "Connection first.",
            tone = StatusTone.Warning,
        )

        // Not an error: nothing is broken on the user's side and there is
        // nothing for them to fix on the phone. It explains why the sections
        // below are read-only.
        state.connected && server.settingsProblem != null -> HubBanner(
            text = server.settingsProblem +
                " The sections below are read-only until the server is " +
                "updated. Chat is unaffected.",
            tone = StatusTone.Warning,
        )

        !state.connected && !state.loading -> HubBanner(
            text = state.error
                ?: "Could not reach Aura. Check the server address, or try again.",
            tone = StatusTone.Bad,
        )

        server.restartRequired -> HubBanner(
            text = "Some changes are saved but need an Aura restart to take effect.",
            tone = StatusTone.Warning,
        )

        else -> null
    }
}
