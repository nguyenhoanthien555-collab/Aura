package com.aura.companion.ui.hub

import com.aura.companion.data.AuraError
import com.aura.companion.data.remote.AppConfigDto
import com.aura.companion.data.remote.EffectiveConfigDto
import com.aura.companion.data.remote.LlmConfigDto
import com.aura.companion.data.remote.MemoryConfigDto
import com.aura.companion.data.remote.ProactiveConfigDto
import com.aura.companion.data.remote.ProviderDto
import com.aura.companion.data.remote.ProviderHealthDto
import com.aura.companion.data.remote.ScreenConfigDto
import com.aura.companion.data.remote.ServerConfigDto
import com.aura.companion.data.settings.AuraSettings
import com.aura.companion.ui.components.StatusTone
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What the hub's front page claims, asserted.
 *
 * These used to be `when` blocks inside `StatusCard`, where the app's most
 * visible sentence was also its least testable one - this module has no JVM
 * Compose harness and no Robolectric, so a verdict inside a `@Composable`
 * cannot be asserted at all. `HubOverview.kt` is that logic pulled out, and
 * this is the reason it was worth pulling out.
 *
 * The case that matters most is the one that was a bug: `/api/health` 200 +
 * `/api/settings` 404 must read as **Connected**, with the settings failure
 * as a second line. See [ServerReachTest] for the ladder underneath.
 */
class HubOverviewTest {

    // ------------------------------------------------------------------
    // The headline
    // ------------------------------------------------------------------

    @Test
    fun `a server with no settings API is connected, not disconnected`() {

        val headline = hubHeadline(
            state(
                reach = ServerReach.Authenticated,
                loaded = false,
                settingsError = AuraError.NotSupported,
            )
        )

        assertEquals("Connected", headline.title)
        assertEquals("Chat works. Settings unavailable on this server.", headline.detail)

        // Amber, not red: half of Aura is working and nothing on the phone
        // is broken.
        assertEquals(StatusTone.Warning, headline.tone)
    }

    @Test
    fun `a settings failure that is not a 404 gets its own second line`() {

        // The defect: every one of these produced the sentence above, which
        // told the user their server was missing an endpoint it has.
        val details = listOf(
            AuraError.RateLimited,
            AuraError.ServerFailure(500),
            AuraError.Waking,
            AuraError.Timeout,
            AuraError.Incompatible(),
            AuraError.Unauthorized,
            AuraError.Forbidden,
        ).map { error ->

            val headline = hubHeadline(
                state(
                    reach = ServerReach.Authenticated,
                    loaded = false,
                    settingsError = error,
                )
            )

            assertEquals("Connected", headline.title)
            assertTrue(
                "$error must not read as a missing endpoint: ${headline.detail}",
                !headline.detail.contains("Settings unavailable on this server"),
            )
            headline.detail
        }

        // And each is distinguishable, not one sentence reused.
        assertEquals(details.size, details.distinct().size)
    }

    @Test
    fun `no headline blames an unexpected response`() {

        // Rule 16: a status code is not an explanation. Every state this
        // screen can reach has to produce a sentence a person can act on.
        val states = listOf(
            state(ServerReach.Unknown, loaded = false, configured = false),
            state(ServerReach.Unknown, loaded = false),
            state(ServerReach.Unreachable, loaded = false),
            state(ServerReach.Connected, loaded = false),
            state(ServerReach.Authenticated, loaded = false),
            state(ServerReach.SettingsAvailable, loaded = true),
            state(ServerReach.ProviderHealthy, loaded = true),
            state(ServerReach.Unknown, loaded = false, loading = true),
        )

        states.forEach { s ->

            val headline = hubHeadline(s)

            assertTrue(
                "a headline must say something: $headline",
                headline.title.isNotBlank() && headline.detail.isNotBlank(),
            )

            listOf("unexpected response", "404", "500", "null").forEach { junk ->
                assertTrue(
                    "\"${headline.detail}\" should not contain \"$junk\"",
                    !headline.detail.contains(junk, ignoreCase = true),
                )
            }
        }
    }

    @Test
    fun `a fallback is reported as a fallback, and names who is answering`() {

        val headline = hubHeadline(
            state(
                reach = ServerReach.SettingsAvailable,
                loaded = true,
                providers = listOf(provider("groq", label = "Groq")),
                health = ProviderHealthDto(
                    requested = "gemini",
                    active = "groq",
                    inFallback = true,
                    ready = true,
                ),
            )
        )

        assertEquals("Running on a fallback", headline.title)
        assertTrue(headline.detail.contains("Groq"))
        assertEquals(StatusTone.Warning, headline.tone)
    }

    @Test
    fun `a healthy server names its provider`() {

        val headline = hubHeadline(
            state(
                reach = ServerReach.ProviderHealthy,
                loaded = true,
                providers = listOf(provider("gemini", label = "Google Gemini")),
                health = ProviderHealthDto(active = "gemini", ready = true),
            )
        )

        assertEquals("Connected", headline.title)
        assertEquals("Google Gemini is answering", headline.detail)
        assertEquals(StatusTone.Good, headline.tone)
    }

    @Test
    fun `a token that was refused is not a connection problem`() {

        val headline = hubHeadline(state(ServerReach.Connected, loaded = false))

        assertEquals("Server reachable", headline.title)
        assertTrue(headline.detail.contains("token"))
        assertEquals(StatusTone.Bad, headline.tone)
    }

    @Test
    fun `an app with no server address is told to set one`() {

        val headline = hubHeadline(
            state(ServerReach.Unknown, loaded = false, configured = false)
        )

        assertEquals("Not set up", headline.title)
        assertTrue(headline.detail.contains("server address"))
    }

    // ------------------------------------------------------------------
    // Only one thing is allowed to move
    // ------------------------------------------------------------------

    @Test
    fun `only the loading state is busy`() {

        // `busy` is what creates the repeating animation, so anything else
        // returning true here would leave a phone rendering frames forever
        // for a status that had already settled.
        assertTrue(hubHeadline(state(ServerReach.Unknown, false, loading = true)).busy)

        listOf(
            state(ServerReach.ProviderHealthy, loaded = true),
            state(ServerReach.Authenticated, loaded = false),
            state(ServerReach.Connected, loaded = false),
            state(ServerReach.Unreachable, loaded = false),
        ).forEach {
            assertTrue("settled states must not animate", !hubHeadline(it).busy)
        }
    }

    @Test
    fun `a refresh over a live connection does not blank the verdict`() {

        // Loading arrives *with* a state that is already known. Reporting
        // "Connecting…" over a working connection would make every pull to
        // refresh look like a dropout.
        val headline = hubHeadline(
            state(ServerReach.ProviderHealthy, loaded = true, loading = true)
        )

        assertEquals("Connected", headline.title)
    }

    // ------------------------------------------------------------------
    // The tiles
    // ------------------------------------------------------------------

    @Test
    fun `there is one tile per kind, each opening its own section`() {

        val tiles = hubTiles(state(ServerReach.SettingsAvailable, loaded = true))

        assertEquals(HubTileKind.entries.toList(), tiles.map { it.kind })

        assertEquals(
            listOf(
                HubRoutes.MODELS,
                HubRoutes.MEMORY,
                HubRoutes.AWARENESS,
                HubRoutes.PROACTIVE,
            ),
            tiles.map { it.route },
        )
    }

    @Test
    fun `an unreported setting reads as unknown, never as off`() {

        // Before the settings document arrives, "Off" would be a guess about
        // someone's privacy - and the reassuring direction of the guess,
        // which is the worse one to be wrong in.
        val tiles = hubTiles(state(ServerReach.Authenticated, loaded = false))
            .associateBy { it.kind }

        assertEquals("—", tiles[HubTileKind.Memory]?.value)
        assertEquals("—", tiles[HubTileKind.Proactive]?.value)
        assertEquals("—", tiles[HubTileKind.Awareness]?.value)
    }

    @Test
    fun `awareness names the switch that is off`() {

        val serverOnly = hubTiles(
            state(
                ServerReach.SettingsAvailable, loaded = true,
                screenOnServer = true, screenOnPhone = false,
            )
        ).first { it.kind == HubTileKind.Awareness }

        assertEquals("Phone off", serverOnly.value)

        val phoneOnly = hubTiles(
            state(
                ServerReach.SettingsAvailable, loaded = true,
                screenOnServer = false, screenOnPhone = true,
            )
        ).first { it.kind == HubTileKind.Awareness }

        assertEquals("Server off", phoneOnly.value)
    }

    @Test
    fun `a phone that is capturing the screen says so in amber`() {

        val tile = hubTiles(
            state(
                ServerReach.SettingsAvailable, loaded = true,
                screenOnServer = true, screenOnPhone = true,
            )
        ).first { it.kind == HubTileKind.Awareness }

        assertEquals("Watching", tile.value)

        // Amber for a capability that is on, not green. Nothing is wrong -
        // but "Aura can see my screen" is a fact that should catch the eye
        // on the front page rather than blend into it.
        assertEquals(StatusTone.Warning, tile.tone)
    }

    @Test
    fun `proactive off is not a warning`() {

        // It ships off, and off is the correct state. Colouring the default
        // amber would train the user to ignore the colour.
        val tile = hubTiles(state(ServerReach.SettingsAvailable, loaded = true))
            .first { it.kind == HubTileKind.Proactive }

        assertEquals("Off", tile.value)
        assertEquals(StatusTone.Neutral, tile.tone)
    }

    @Test
    fun `the provider tile follows the chain, not the request`() {

        val tile = hubTiles(
            state(
                ServerReach.SettingsAvailable,
                loaded = true,
                providers = listOf(provider("groq", label = "Groq")),
                health = ProviderHealthDto(
                    requested = "gemini", active = "groq",
                    inFallback = true, ready = true,
                ),
            )
        ).first { it.kind == HubTileKind.Provider }

        assertEquals("Groq", tile.value)
        assertEquals(StatusTone.Warning, tile.tone)
    }

    @Test
    fun `a provider this build has no label for is still named`() {

        // A server newer than the app. Showing a dash would be worse than
        // showing the raw name the server used.
        val state = state(
            ServerReach.SettingsAvailable,
            loaded = true,
            health = ProviderHealthDto(active = "something-new", ready = true),
        )

        assertEquals("something-new", state.activeProviderLabel)
    }

    // ------------------------------------------------------------------
    // The banner
    // ------------------------------------------------------------------

    @Test
    fun `a healthy hub shows no banner at all`() {

        assertNull(
            hubBanner(
                state(
                    ServerReach.ProviderHealthy, loaded = true,
                    health = ProviderHealthDto(active = "gemini", ready = true),
                )
            )
        )
    }

    @Test
    fun `an unconfigured app is told what to do first`() {

        val banner = hubBanner(
            state(ServerReach.Unknown, loaded = false, configured = false)
        )

        assertNotNull(banner)
        assertTrue(banner!!.text.contains("Connection"))
        assertEquals(StatusTone.Warning, banner.tone)
    }

    @Test
    fun `a missing settings API is a warning, not an error`() {

        val banner = hubBanner(
            state(
                ServerReach.Authenticated, loaded = false,
                settingsError = AuraError.NotSupported,
            )
        )

        assertNotNull(banner)
        assertEquals(StatusTone.Warning, banner!!.tone)
        assertTrue(banner.text.contains("read-only"))
        assertTrue(banner.text.contains("Chat is unaffected"))

        // The advice only applies to a server that is genuinely behind.
        assertTrue(banner.text.contains("until the server is updated"))
    }

    @Test
    fun `a transient settings failure is not advice to update the server`() {

        val banner = hubBanner(
            state(
                ServerReach.Authenticated, loaded = false,
                settingsError = AuraError.RateLimited,
            )
        )

        assertNotNull(banner)
        assertTrue(banner!!.text.contains("read-only"))
        assertTrue(banner.text.contains("Chat is unaffected"))

        // Neither of the two claims the old banner made about every failure.
        assertTrue(!banner.text.contains("until the server is updated"))
        assertTrue(banner.text.contains("Pull down to try again"))
    }

    @Test
    fun `a refresh in flight does not raise an error banner`() {

        // The first composition has reach Unknown and loading true. A red
        // "could not reach Aura" there would be a claim about a request that
        // has not come back yet.
        assertNull(hubBanner(state(ServerReach.Unknown, loaded = false, loading = true)))
    }

    // ------------------------------------------------------------------

    private fun state(
        reach: ServerReach,
        loaded: Boolean,
        loading: Boolean = false,
        configured: Boolean = true,
        settingsError: AuraError? = null,
        providers: List<ProviderDto> = emptyList(),
        health: ProviderHealthDto = ProviderHealthDto(),
        screenOnServer: Boolean = false,
        screenOnPhone: Boolean = false,
        llm: LlmConfigDto = LlmConfigDto(),
    ) = HubUiState(
        device = AuraSettings(
            serverUrl = if (configured) "https://aura.example/" else "",
            authToken = if (configured) "test-token" else "",
            screenObservationEnabled = screenOnPhone,
        ),
        loading = loading,
        server = ServerState(
            loaded = loaded,
            reach = reach,
            settingsError = settingsError,
            config = EffectiveConfigDto(
                app = AppConfigDto(version = "0.2.0"),
                llm = llm,
                memory = MemoryConfigDto(),
                proactive = ProactiveConfigDto(),
                server = ServerConfigDto(
                    screen = ScreenConfigDto(enabled = screenOnServer),
                ),
            ),
            providers = providers,
            health = health,
        ),
    )

    private fun provider(
        name: String,
        label: String = "",
        model: String = "",
        modelSetting: String = "",
    ) = ProviderDto(
        name = name,
        label = label,
        chat = true,
        configured = true,
        model = model,
        modelSetting = modelSetting,
    )
}
