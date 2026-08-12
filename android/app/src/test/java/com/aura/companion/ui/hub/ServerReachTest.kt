package com.aura.companion.ui.hub

import com.aura.companion.data.AuraError
import com.aura.companion.data.remote.EffectiveConfigDto
import com.aura.companion.data.remote.ProviderHealthDto
import com.aura.companion.data.remote.ProviderStateDto
import com.aura.companion.data.settings.AuraSettings
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The bug this phase existed to fix, as an assertion.
 *
 * WHAT WENT WRONG
 * ---------------
 * Connectivity was one boolean carrying two unrelated facts: "Aura
 * answered" and "Aura gave me its settings". A deployment older than the
 * app answers `/api/health` and `/api/chat` perfectly and 404s
 * `/api/settings`, so the app read a working server as disconnected and
 * told the user to check a connection that was fine.
 *
 * [ServerReach] is the fix: an ordered ladder where each rung is one
 * independently observed fact, and [HubUiState.connected] is anchored to
 * the health rung rather than the settings rung.
 *
 * WHY THIS IS NOT A VIEWMODEL TEST
 * --------------------------------
 * `HubViewModel` takes a `SettingsStore`, which needs a `Context` and a
 * Keystore-backed key and therefore cannot exist on the JVM. The logic
 * that was wrong is not in the ViewModel anyway - it is in these derived
 * properties, which are pure functions of the state the ViewModel
 * assembles. Testing them directly tests the actual defect, and
 * `SettingsContractTest` covers the requests that feed them.
 */
class ServerReachTest {

    // ------------------------------------------------------------------
    // The ladder itself
    // ------------------------------------------------------------------

    @Test
    fun `the rungs are ordered from nothing to fully healthy`() {

        // `atLeast` is ordinal comparison, so the declaration order *is*
        // the contract. A rung inserted in the wrong place would silently
        // change what every screen believes.
        assertEquals(
            listOf(
                ServerReach.Unknown,
                ServerReach.Unreachable,
                ServerReach.Connected,
                ServerReach.Authenticated,
                ServerReach.SettingsAvailable,
                ServerReach.ProviderHealthy,
            ),
            ServerReach.entries.toList(),
        )
    }

    @Test
    fun `a higher rung satisfies every lower one`() {

        assertTrue(ServerReach.ProviderHealthy.atLeast(ServerReach.Authenticated))
        assertTrue(ServerReach.SettingsAvailable.atLeast(ServerReach.Connected))
        assertTrue(ServerReach.Authenticated.atLeast(ServerReach.Authenticated))

        assertFalse(ServerReach.Connected.atLeast(ServerReach.Authenticated))
        assertFalse(ServerReach.Unreachable.atLeast(ServerReach.Connected))
        assertFalse(ServerReach.Unknown.atLeast(ServerReach.Unreachable))
    }

    // ------------------------------------------------------------------
    // Connected means "Aura answered", not "Aura is configurable"
    // ------------------------------------------------------------------

    @Test
    fun `a server whose settings API is missing is still connected`() {

        // Exactly the reported failure: /api/health 200, /api/settings 404.
        val state = state(
            reach = ServerReach.Authenticated,
            loaded = false,
            settingsError = AuraError.NotSupported,
        )

        assertTrue("a 404 from settings must not read as disconnected", state.connected)
        assertFalse(state.settingsAvailable)
    }

    @Test
    fun `that server explains itself instead of blaming the connection`() {

        // The premise is a 404 and it is now stated as one. The fixture used
        // to leave the error null and still assert this sentence, which is
        // how the sentence came to be shown for every other settings failure
        // too - see SettingsAccessTest.
        val state = state(
            reach = ServerReach.Authenticated,
            loaded = false,
            settingsError = AuraError.NotSupported,
        )

        // Not "Connect to Aura to change this" - the user is connected, and
        // sending them to the connection screen would have them retype a
        // token that was accepted.
        assertEquals(
            "This Aura server does not expose settings",
            state.lockedReason("tools.enabled"),
        )
    }

    @Test
    fun `a settings failure that is not a 404 does not claim a missing feature`() {

        // The defect this phase fixed. Every one of these reached the "does
        // not expose settings" sentence, because the reason was a boolean.
        listOf(
            AuraError.Unauthorized,
            AuraError.Forbidden,
            AuraError.RateLimited,
            AuraError.ServerFailure(500),
            AuraError.Waking,
            AuraError.Timeout,
            AuraError.Offline,
            AuraError.Incompatible(),
            AuraError.Unknown(),
        ).forEach { error ->

            val reason = state(
                reach = ServerReach.Authenticated,
                loaded = false,
                settingsError = error,
            ).lockedReason("tools.enabled")

            assertNotNull("$error must still explain itself", reason)
            assertFalse(
                "$error must not read as a missing endpoint: $reason",
                reason!!.contains("does not expose"),
            )
        }
    }

    @Test
    fun `a server still answering does not claim anything yet`() {

        // Health returned 200 and the settings request has not come back. The
        // old code called this "does not expose settings" - a verdict on a
        // request still in flight.
        val state = state(reach = ServerReach.Authenticated, loaded = false)

        assertEquals(SettingsAccess.Loading, state.settingsAccess)
        assertFalse(state.lockedReason("tools.enabled")!!.contains("does not expose"))
    }

    @Test
    fun `an unreachable server does send the user to the connection screen`() {

        val state = state(reach = ServerReach.Unreachable, loaded = false)

        assertFalse(state.connected)
        assertEquals(
            "Connect to Aura to change this",
            state.lockedReason("tools.enabled"),
        )
    }

    @Test
    fun `something answered but refused the token is not connected`() {

        // The Connected rung on its own: a host responded, Aura did not
        // accept us. `/api/health` is itself behind the token, so this is
        // the only thing a 401 there can mean.
        val state = state(reach = ServerReach.Connected, loaded = false)

        assertFalse(state.connected)
    }

    @Test
    fun `a healthy provider chain is connected too`() {

        val state = state(reach = ServerReach.ProviderHealthy, loaded = true)

        assertTrue(state.connected)
        assertTrue(state.settingsAvailable)
        assertNull(state.lockedReason("tools.enabled"))
    }

    @Test
    fun `the first render claims nothing`() {

        val fresh = HubUiState()

        assertEquals(ServerReach.Unknown, fresh.server.reach)
        assertFalse(fresh.connected)
        assertFalse(fresh.settingsAvailable)
    }

    // ------------------------------------------------------------------
    // Feature locks: what the phone may offer this particular server
    // ------------------------------------------------------------------

    @Test
    fun `a path the server does not list is locked with its own reason`() {

        val state = state(
            reach = ServerReach.SettingsAvailable,
            loaded = true,
            configurable = setOf("tools.enabled", "memory.recall"),
        )

        assertTrue(state.supports("tools.enabled"))
        assertNull(state.lockedReason("tools.enabled"))

        assertFalse(state.supports("voice.tts.volume"))
        assertEquals(
            "This Aura server does not support this setting",
            state.lockedReason("voice.tts.volume"),
        )
    }

    @Test
    fun `an empty configurable list is treated as everything, not nothing`() {

        // Before the first successful load, and on a server that does not
        // report the field. Greying out every control because a request is
        // still in flight would be worse than a rejection the user can read.
        val state = state(
            reach = ServerReach.SettingsAvailable,
            loaded = true,
            configurable = emptySet(),
        )

        assertTrue(state.supports("voice.tts.volume"))
        assertNull(state.lockedReason("voice.tts.volume"))
    }

    @Test
    fun `the closest reason wins when more than one applies`() {

        // Unreachable *and* the path is unsupported. "Connect to Aura" is
        // the actionable one; "does not support this setting" would be a
        // claim about a server we never reached.
        val state = state(
            reach = ServerReach.Unreachable,
            loaded = false,
            configurable = setOf("memory.recall"),
        )

        assertEquals(
            "Connect to Aura to change this",
            state.lockedReason("voice.tts.volume"),
        )
    }

    @Test
    fun `every path this phase added is unlocked by a server that lists it`() {

        val added = setOf(
            "server.screen.min_interval",
            "tools.enabled",
            "tools.auto_approve",
            "tools.timeout",
            "voice.tts.provider",
            "voice.tts.voice",
            "voice.tts.volume",
            "voice.tts.playback",
        )

        val state = state(
            reach = ServerReach.SettingsAvailable,
            loaded = true,
            configurable = added,
        )

        added.forEach { path ->
            assertNull("$path should be editable", state.lockedReason(path))
        }
    }

    // ------------------------------------------------------------------
    // What Diagnostics reads off the state
    // ------------------------------------------------------------------

    @Test
    fun `an isolated provider failure leaves the rest of the map readable`() {

        val state = state(
            reach = ServerReach.SettingsAvailable,
            loaded = true,
            health = ProviderHealthDto(
                requested = "gemini",
                active = "groq",
                chain = listOf("gemini", "groq"),
                inFallback = true,
                ready = true,
                providers = mapOf(
                    "groq" to ProviderStateDto(
                        configured = true, healthy = true,
                        state = "active", inChain = true,
                    ),
                    "ollama" to ProviderStateDto(
                        state = "error", problem = "RuntimeError",
                    ),
                ),
            ),
        )

        // Connected, serving, and on a substitute - three separate facts,
        // and none of them is "broken".
        assertTrue(state.connected)
        assertTrue(state.server.health.inFallback)
        assertTrue(state.server.health.ready)

        assertEquals("active", state.server.health.providers["groq"]?.state)
        assertEquals("RuntimeError", state.server.health.providers["ollama"]?.problem)
    }

    @Test
    fun `a server that reports no chain is not treated as a failed one`() {

        val state = state(reach = ServerReach.Authenticated, loaded = false)

        // Empty rather than false-y in a way the UI would colour red: the
        // Diagnostics row reads "Not reported" from this.
        assertTrue(state.server.health.chain.isEmpty())
        assertFalse(state.server.health.inFallback)
        assertTrue(state.connected)
    }

    // ------------------------------------------------------------------

    private fun state(
        reach: ServerReach,
        loaded: Boolean,
        settingsError: AuraError? = null,
        configurable: Set<String> = emptySet(),
        health: ProviderHealthDto = ProviderHealthDto(),
    ) = HubUiState(
        device = AuraSettings(
            serverUrl = "https://aura.example/",
            authToken = "test-token",
        ),
        server = ServerState(
            loaded = loaded,
            reach = reach,
            settingsError = settingsError,
            config = EffectiveConfigDto(),
            configurable = configurable,
            health = health,
        ),
    )
}
