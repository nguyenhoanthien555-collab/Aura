package com.aura.companion.ui.hub

import com.aura.companion.data.AuraError
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.settings.FakeSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.Job
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import androidx.lifecycle.viewModelScope
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The hub, from three real HTTP routes to the state the screens render.
 *
 * WHY THIS CLASS EXISTS
 * ---------------------
 * `AuraRepositoryTest` proves an HTTP status becomes the right [AuraError],
 * and `SettingsAccessTest` proves an [AuraError] becomes the right sentence.
 * Neither proves the wiring between them, and the wiring is where the defect
 * lived: `/api/health` 200 followed by *any* settings failure reported "this
 * Aura server does not expose settings", on a deployment that has the route
 * and had just accepted the same token for the health check.
 *
 * So these drive the whole path - `GET /api/health`, `GET /api/settings`,
 * `GET /api/providers`, `PATCH /api/settings` - against a server on loopback,
 * and assert on [HubUiState]. A regression anywhere between the socket and
 * the screen fails here.
 *
 * `runBlocking` and a routing dispatcher, not `runTest` and a response queue:
 * [AuraRepository] works inside `withContext(Dispatchers.IO)`, which no test
 * scheduler controls, and `init` calls `refresh()` - so a queue would hand
 * the health request whichever response the test enqueued first. Routing by
 * path removes the ordering dependency; the waits below are on the state
 * itself, with a real-time timeout.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class HubViewModelTest {

    private lateinit var server: MockWebServer
    private lateinit var settings: FakeSettings

    /** Set by a test before it builds the ViewModel: `init` already loads. */
    private var healthRoute: () -> MockResponse = { ok(HEALTH) }
    private var settingsRoute: () -> MockResponse = { ok(SETTINGS) }
    private var patchRoute: () -> MockResponse = { ok(patched()) }
    private var providersRoute: () -> MockResponse = { ok(PROVIDERS) }
    private var chainRoute: () -> MockResponse = { ok(CHAIN) }

    /** Every ViewModel built, so teardown can stop its coroutines. */
    private val viewModels = mutableListOf<HubViewModel>()

    /** What the server was asked to change, in order. */
    private val patches = mutableListOf<String>()

    @Before
    fun setUp() {

        Dispatchers.setMain(Dispatchers.Unconfined)

        server = MockWebServer()

        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {

                val path = request.path.orEmpty()

                // Longest prefix first: /api/providers/health also starts
                // with /api/providers.
                return when {
                    path.startsWith("/api/providers/health") -> chainRoute()
                    path.startsWith("/api/providers") -> providersRoute()

                    path.startsWith("/api/settings") ->
                        if (request.method == "PATCH") {
                            patches += request.body.readUtf8()
                            patchRoute()
                        } else {
                            settingsRoute()
                        }

                    path.startsWith("/api/health") -> healthRoute()
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }

        server.start()
    }

    /**
     * Stop the ViewModels, then the server, then the dispatcher.
     *
     * The order matters for the same reason it does in `ChatViewModelTest`:
     * `viewModelScope` reads `Dispatchers.Main` on every dispatch, and
     * `resetMain` throws if something is reading it. `init` starts a
     * collector on the settings flow that never completes on its own, so
     * every one of these has work in flight at the end of the test.
     */
    @After
    fun tearDown() {

        runBlocking {
            viewModels.forEach { viewModel ->

                val job = viewModel.viewModelScope.coroutineContext[Job]
                    ?: return@forEach

                withTimeoutOrNull(TIMEOUT_MS) { job.cancelAndJoin() }
                    ?: throw AssertionError("a ViewModel did not stop in ${TIMEOUT_MS}ms")
            }
        }

        viewModels.clear()

        if (::server.isInitialized) server.shutdown()

        Dispatchers.resetMain()
    }

    // ------------------------------------------------------------------
    // A settings document that arrives
    // ------------------------------------------------------------------

    @Test
    fun `a server that answers both routes has its settings available`() {

        val state = await(hub(), "the settings document") { it.server.loaded }

        // Criterion B: a live 200 from /api/settings is recognised as such.
        assertEquals(SettingsAccess.Available, state.settingsAccess)
        assertTrue(state.settingsAvailable)
        assertTrue(state.connected)
        assertTrue(state.server.reach.atLeast(ServerReach.SettingsAvailable))

        // Parsed, not merely received.
        assertEquals("gemini", state.server.config.llm.provider)
        assertEquals(0.7, state.server.config.llm.temperature, 0.0001)

        // Nothing is locked, and no banner claims anything.
        assertNull(state.lockedReason("llm.provider"))
        assertNull(state.error)
        assertNull(settingsBanner(state.settingsAccess))
        assertNull(hubBanner(state))
    }

    @Test
    fun `the provider chain raises the top rung and names who is answering`() {

        val state = await(hub(), "the chain") { it.server.health.active.isNotEmpty() }

        assertEquals(ServerReach.ProviderHealthy, state.server.reach)
        assertEquals("Google Gemini", state.activeProviderLabel)
        assertEquals("gemini-3.6-flash", state.activeModel)
        assertNull(state.server.providersError)
    }

    // ------------------------------------------------------------------
    // A settings document that does not
    //
    // One test per outcome the audit named, each asserting the *whole* claim
    // the hub makes: the state, the sentence on a locked row, and that chat
    // is still reported as working.
    // ------------------------------------------------------------------

    @Test
    fun `a 404 is the one outcome allowed to say the route is missing`() {

        settingsRoute = { MockResponse().setResponseCode(404) }

        val state = failedSettings()

        assertEquals(SettingsAccess.NotExposed, state.settingsAccess)

        // Still connected: chat is on the same token and the same server.
        assertTrue(state.connected)
        assertNull(state.error)

        assertEquals(
            "This Aura server does not expose settings",
            state.lockedReason("llm.provider"),
        )
    }

    @Test
    fun `a token refused for settings alone is not a missing feature`() {

        settingsRoute = { MockResponse().setResponseCode(401) }

        val state = failedSettings()

        assertEquals(SettingsAccess.AuthRequired, state.settingsAccess)
        assertTrue(state.connected)

        // The health check accepted this token a moment ago, so blaming the
        // server's build would send the user to fix the wrong thing.
        assertFalse(state.lockedReason("llm.provider")!!.contains("does not expose"))
        assertTrue(settingsNotice(state.settingsAccess)!!.contains("same token"))
    }

    @Test
    fun `every settings failure keeps its own identity all the way up`() {

        // The defect, as a table. Each of these used to arrive at the screen
        // as "this Aura server does not expose settings".
        val expected = mapOf(
            403 to SettingsAccess.Forbidden,
            422 to SettingsAccess.Refused,
            429 to SettingsAccess.RateLimited,
            500 to SettingsAccess.ServerError,
            // A bare 502/503/504 from a free-tier edge is a container that has
            // not finished booting, and the one instruction that works is
            // "wait a moment", not "update your server".
            502 to SettingsAccess.Waking,
            503 to SettingsAccess.Waking,
        )

        expected.forEach { (code, access) ->

            settingsRoute = { MockResponse().setResponseCode(code) }

            val state = failedSettings()

            assertEquals("HTTP $code", access, state.settingsAccess)
            assertTrue("HTTP $code must stay connected", state.connected)
            assertFalse(
                "HTTP $code claimed a missing endpoint",
                state.lockedReason("llm.provider")!!.contains("does not expose"),
            )
        }
    }

    @Test
    fun `a body this build cannot read is a version mismatch`() {

        settingsRoute = { ok("{ \"effective\": { \"llm\": ") }

        val state = failedSettings()

        assertEquals(SettingsAccess.Incompatible, state.settingsAccess)

        val reason = state.lockedReason("llm.provider")!!
        assertTrue(reason.contains("could not read"))
        assertFalse(reason.contains("does not expose"))

        // And nothing from the parser reaches the screen: a serialization
        // message quotes the JSON it choked on.
        assertFalse(reason.contains("JsonDecodingException"))
        assertFalse(reason.contains("effective"))
    }

    @Test
    fun `a health failure is a connection problem, not a settings one`() {

        healthRoute = { MockResponse().setResponseCode(401) }

        val state = await(hub(), "the health failure") { it.error != null }

        // Nothing is known about settings, because nothing asked. Claiming
        // a missing feature from here would be a verdict on an unsent request.
        assertEquals(ServerReach.Connected, state.server.reach)
        assertEquals(SettingsAccess.NotConnected, state.settingsAccess)
        assertNull(state.server.settingsError)
        assertFalse(state.connected)
    }

    // ------------------------------------------------------------------
    // Saving a setting
    // ------------------------------------------------------------------

    @Test
    fun `a saved setting shows the server's value, not the one that was sent`() {

        // The server clamped it: 1.4 asked for, 0.9 in effect.
        patchRoute = { ok(patched(temperature = 0.9)) }

        val viewModel = hub()
        await(viewModel, "the first load") { it.server.loaded }

        viewModel.setNumber("llm.temperature", 1.4)

        val state = await(viewModel, "the save") {
            it.pending.isEmpty() && it.server.config.llm.temperature == 0.9
        }

        // Criterion F. A row that kept showing 1.4 would be a screen
        // disagreeing with the server it is configuring.
        assertEquals(0.9, state.server.config.llm.temperature, 0.0001)
        assertTrue(patches.single().contains("1.4"))
    }

    @Test
    fun `a refused setting says what the server said and changes nothing`() {

        patchRoute = {
            MockResponse()
                .setResponseCode(422)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"detail": {"error": "invalid_setting",
                       "message": "llm.temperature must be between 0.0 and 2.0"}}"""
                )
        }

        val viewModel = hub()
        await(viewModel, "the first load") { it.server.loaded }

        viewModel.setNumber("llm.temperature", 9.0)

        val state = await(viewModel, "the refusal") { it.notice != null }

        assertEquals(Notice.Kind.Error, state.notice!!.kind)

        // The server's own sentence, which names the path and the bounds.
        assertTrue(state.notice.text.contains("llm.temperature"))
        assertFalse(state.notice.text.contains("422"))

        // And the value the server still holds, not the rejected one.
        assertEquals(0.7, state.server.config.llm.temperature, 0.0001)
        assertTrue(state.pending.isEmpty())
    }

    @Test
    fun `a setting that needs a restart is saved and says so`() {

        patchRoute = {
            ok(
                """
                {"applied": [], "restart_required": ["voice.tts.provider"],
                 "persistent": true, "needs_restart": true,
                 "effective": {"llm": {"provider": "gemini", "temperature": 0.7}}}
                """
            )
        }

        val viewModel = hub()
        await(viewModel, "the first load") { it.server.loaded }

        viewModel.setText("voice.tts.provider", "edge")

        val state = await(viewModel, "the save") { it.notice != null }

        assertEquals(Notice.Kind.Warning, state.notice!!.kind)
        assertTrue(state.notice.text.contains("Restart"))
        assertTrue(state.server.restartRequired)
    }

    @Test
    fun `a setting saved without durable storage says that instead`() {

        // No AURA_SECRET_KEY on the host: applied now, gone at restart.
        patchRoute = { ok(patched(persistent = false)) }

        val viewModel = hub()
        await(viewModel, "the first load") { it.server.loaded }

        viewModel.setFlag("memory.recall", true)

        val state = await(viewModel, "the save") { it.notice != null }

        assertEquals(Notice.Kind.Warning, state.notice!!.kind)
        assertTrue(state.notice.text.contains("not survive"))
    }

    @Test
    fun `a reload after a save shows what the server now reports`() {

        val viewModel = hub()
        await(viewModel, "the first load") { it.server.loaded }

        patchRoute = { ok(patched(temperature = 0.2)) }
        viewModel.setNumber("llm.temperature", 0.2)
        await(viewModel, "the save") { it.server.config.llm.temperature == 0.2 }

        // The next GET is the one that decides, and it agrees - with a second
        // change made server-side, so this cannot pass on the patch's echo.
        settingsRoute = {
            ok(
                SETTINGS
                    .replace("\"temperature\": 0.7", "\"temperature\": 0.2")
                    .replace("\"history_limit\": 10", "\"history_limit\": 42")
            )
        }
        viewModel.refresh()

        val state = await(viewModel, "the reload") {
            it.server.loaded && it.server.config.memory.historyLimit == 42
        }

        assertEquals(0.2, state.server.config.llm.temperature, 0.0001)
        assertEquals(SettingsAccess.Available, state.settingsAccess)
    }

    // ------------------------------------------------------------------
    // Providers, and where a model is written
    // ------------------------------------------------------------------

    @Test
    fun `the model picker writes the setting the server named for that provider`() {

        // Anthropic primary. `brain/router.py` reads `llm.anthropic_model`
        // for it; `llm.model` is Gemini's and only Gemini's.
        settingsRoute = { ok(SETTINGS.replace("\"provider\": \"gemini\"", "\"provider\": \"anthropic\"")) }

        val viewModel = hub()

        val state = await(viewModel, "the providers") {
            it.server.loaded && it.server.providers.isNotEmpty()
        }

        assertEquals("llm.anthropic_model", state.modelSetting)
        assertEquals("claude-sonnet-5", state.activeModel)

        viewModel.setModel("claude-opus-5")

        await(viewModel, "the model change") { it.pending.isEmpty() && patches.isNotEmpty() }

        val sent = patches.single()
        assertTrue("sent: $sent", sent.contains("anthropic_model"))
        assertTrue("sent: $sent", sent.contains("claude-opus-5"))

        // Criterion G: nothing was written to Gemini's key on Anthropic's
        // behalf, which is a control that appears to work and cannot.
        assertFalse("sent: $sent", sent.contains("\"model\""))
    }

    @Test
    fun `a provider route that fails is reported rather than left blank`() {

        providersRoute = { MockResponse().setResponseCode(500) }

        val state = await(hub(), "the provider failure") { it.server.providersError != null }

        assertEquals(AuraError.ServerFailure(500), state.server.providersError)

        // And it takes nothing else down with it: the settings document
        // arrived, so the rest of the hub still renders.
        assertTrue(state.server.loaded)
        assertEquals(SettingsAccess.Available, state.settingsAccess)
        assertTrue(state.server.providers.isEmpty())
    }

    @Test
    fun `a chain that cannot be built does not raise the top rung`() {

        chainRoute = {
            ok(
                """
                {"requested": "gemini", "active": "", "chain": [],
                 "in_fallback": false, "ready": false,
                 "problems": ["provider chain unavailable (ValueError)"],
                 "providers": {}}
                """
            )
        }

        val state = await(hub(), "the chain") { it.server.health.problems.isNotEmpty() }

        assertEquals(ServerReach.SettingsAvailable, state.server.reach)
        assertFalse(state.server.health.ready)
    }

    // ------------------------------------------------------------------
    // What the server will not let this phone change
    // ------------------------------------------------------------------

    @Test
    fun `a control missing from configurable is locked with its own reason`() {

        // A phone newer than its server. Offering the control anyway would
        // send a PATCH the server answers with a 422.
        settingsRoute = { ok(SETTINGS.replace("\"tools.timeout\",", "")) }

        val state = await(hub(), "the settings document") { it.server.loaded }

        assertEquals(
            "This Aura server does not support this setting",
            state.lockedReason("tools.timeout"),
        )

        // Criterion E: the ones it does accept stay open.
        assertNull(state.lockedReason("llm.provider"))
        assertNull(state.lockedReason("memory.recall"))
    }

    @Test
    fun `capability-granting paths are not configurable at all`() {

        val state = await(hub(), "the settings document") { it.server.loaded }

        // Which tools exist and which files they may touch is a capability,
        // not a setting, and a bearer token is deliberately not enough for
        // it. The server's allow-list omits them; the hub renders that.
        listOf("tools.allowed", "tools.allowed_paths", "tools.applications").forEach {
            assertNotNull("$it must be locked", state.lockedReason(it))
        }
    }

    @Test
    fun `a device toggle is written to the phone and never to the server`() {

        val viewModel = hub()
        await(viewModel, "the settings document") { it.server.loaded }

        viewModel.setScreenObservation(true)

        val state = await(viewModel, "the toggle") { it.device.screenObservationEnabled }

        assertTrue(settings.current.screenObservationEnabled)

        // It gates what this phone does. There is nothing for the server's
        // settings overlay to do with it, so no PATCH was sent.
        assertTrue(patches.isEmpty())
        assertEquals(SettingsAccess.Available, state.settingsAccess)
    }

    // ------------------------------------------------------------------
    // Harness
    // ------------------------------------------------------------------

    /**
     * A hub pointed at the loopback server, registered for teardown.
     *
     * Built after the routes are set, because `init` calls `refresh()` - a
     * ViewModel constructed first would race the test's own setup.
     *
     * The settings are rebuilt here rather than in `setUp` so a test that
     * builds two hubs gets two independent stores, and so the URL is the one
     * MockWebServer actually bound.
     */
    private fun hub(): HubViewModel {

        settings = FakeSettings(
            serverUrl = server.url("/").toString(),
            authToken = "test-token",
        )

        val viewModel = HubViewModel(settings, AuraRepository(settings))

        viewModels += viewModel

        return viewModel
    }

    /** A hub whose settings request failed, whatever the failure was. */
    private fun failedSettings(): HubUiState =
        await(hub(), "the settings failure") { it.server.settingsError != null }

    /**
     * Wait for a state, in real time.
     *
     * `first` on the state flow rather than an advanced test scheduler: the
     * repository's work happens on `Dispatchers.IO` and over a real socket,
     * neither of which a scheduler moves. The timeout is what turns a
     * deadlock into a named failure instead of a hung build - and the
     * message carries the last state, whose `toString` masks the token
     * (see `AuraSettings.toString`).
     */
    private fun await(
        viewModel: HubViewModel,
        what: String,
        predicate: (HubUiState) -> Boolean,
    ): HubUiState = runBlocking {

        try {
            withTimeout(TIMEOUT_MS) { viewModel.state.first(predicate) }
        } catch (e: TimeoutCancellationException) {
            throw AssertionError(
                "$what never arrived within ${TIMEOUT_MS}ms. " +
                    "Last state: ${viewModel.state.value}"
            )
        }
    }

    private fun ok(body: String): MockResponse = MockResponse()
        .setHeader("Content-Type", "application/json")
        .setBody(body)

    /**
     * What `PATCH /api/settings` answers.
     *
     * `effective` is the server's, not the caller's: these tests exist partly
     * to prove the hub renders what came back rather than what it sent, so
     * the value here is deliberately settable per test.
     */
    private fun patched(temperature: Double = 0.7, persistent: Boolean = true): String =
        """
        {
          "applied": ["llm.temperature"],
          "restart_required": [],
          "persistent": $persistent,
          "needs_restart": false,
          "effective": {
            "llm": {"provider": "gemini", "model": "gemini-3.6-flash",
                    "temperature": $temperature},
            "memory": {"history_limit": 10}
          }
        }
        """

    private companion object {

        /** Long enough for a loopback round trip, short enough to fail a hang. */
        const val TIMEOUT_MS = 5_000L

        const val HEALTH = """
            {"status": "ok", "version": "0.11.0", "uptime_seconds": 1284.5,
             "runtime": {"memory": "on", "proactive": "on", "tools": "off"}}
        """

        /**
         * A settings document in the deployed server's shape.
         *
         * Trimmed - `SettingsContractTest` is where the whole live payload is
         * pinned, field by field. What matters here is that the literals the
         * tests rewrite are present exactly once: `"provider": "gemini"`,
         * `"temperature": 0.7`, `"history_limit": 10` and the
         * `"tools.timeout",` entry in the allow-list.
         *
         * `configurable` omits `tools.allowed`, `tools.allowed_paths` and
         * `tools.applications`, as the server's own allow-list does: which
         * tools exist and which files they may reach is a capability, and a
         * bearer token is deliberately not enough to widen it.
         */
        const val SETTINGS = """
            {
              "effective": {
                "llm": {
                  "provider": "gemini",
                  "model": "gemini-3.6-flash",
                  "anthropic_model": "claude-sonnet-5",
                  "fallback_providers": ["groq"],
                  "temperature": 0.7,
                  "max_output_tokens": 768,
                  "timeout": 120.0
                },
                "memory": {"recall": false, "profile": true, "pipeline": true,
                           "history_limit": 10, "retrieval_scope": 500},
                "proactive": {"enabled": false, "max_per_day": 4,
                              "quiet_hours": [[22, 8]]},
                "vision": {"enabled": false, "ollama_model": "qwen2.5vl:7b"},
                "voice": {"tts": {"enabled": false, "provider": "auto"}},
                "tools": {"enabled": false, "timeout": 30.0},
                "server": {"screen": {"enabled": false, "min_interval": 8.0}}
              },
              "providers": {"persistent": true, "persistence_note": ""},
              "configurable": [
                "llm.provider",
                "llm.model",
                "llm.anthropic_model",
                "llm.fallback_providers",
                "llm.temperature",
                "llm.max_output_tokens",
                "memory.recall",
                "memory.profile",
                "memory.history_limit",
                "proactive.enabled",
                "proactive.quiet_hours",
                "vision.enabled",
                "voice.tts.provider",
                "tools.enabled",
                "tools.timeout",
                "server.screen.enabled"
              ]
            }
        """

        /** Two providers: the primary and the one a test makes primary. */
        const val PROVIDERS = """
            {
              "providers": [
                {"name": "gemini", "label": "Google Gemini", "chat": true,
                 "models": ["gemini-3.6-flash", "gemini-3.6-pro"],
                 "configured": true, "is_primary": true,
                 "model": "gemini-3.6-flash", "model_setting": "llm.model",
                 "key_masked": "••••••••7X2", "key_source": "environment",
                 "api_key_env": "GEMINI_API_KEY"},
                {"name": "anthropic", "label": "Anthropic Claude", "chat": true,
                 "models": ["claude-sonnet-5", "claude-opus-5"],
                 "configured": true, "model": "claude-sonnet-5",
                 "model_setting": "llm.anthropic_model",
                 "api_key_env": "ANTHROPIC_API_KEY"}
              ],
              "primary": "gemini",
              "fallback_providers": ["groq"],
              "active_chain": "gemini",
              "key_storage": {"persistent": true, "persistence_note": ""}
            }
        """

        const val CHAIN = """
            {"requested": "gemini", "active": "gemini", "chain": ["gemini"],
             "in_fallback": false, "problems": [], "ready": true,
             "providers": {"gemini": {"configured": true, "healthy": true,
                                      "state": "active", "in_chain": true}}}
        """
    }
}
