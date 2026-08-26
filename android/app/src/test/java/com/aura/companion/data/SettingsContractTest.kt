package com.aura.companion.data

import com.aura.companion.data.settings.FakeSettings
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.double
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * Phase 10: the settings contract, over the wire.
 *
 * WHY A REAL SERVER AND REAL JSON
 * -------------------------------
 * The bugs this layer has are never type errors. `keyMasked` where the
 * server writes `key_masked`, a `Double` where it sends an int, a field
 * the app requires that an older deployment omits - all of those compile
 * and all of them fail at runtime, so the payloads below are pasted in
 * the server's own shape rather than built from the DTOs. If a DTO field
 * name drifts from `server/routes/settings.py`, these fail.
 *
 * The bodies are deliberately *partial* in places. `ignoreUnknownKeys`
 * covers a server newer than the app; the defaults on every DTO field
 * cover the opposite, and that direction is the one this phase is about -
 * a phone talking to a deployment that predates half its endpoints.
 */
class SettingsContractTest {

    private lateinit var server: MockWebServer
    private lateinit var repository: AuraRepository

    private val json = Json { ignoreUnknownKeys = true }

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()

        repository = AuraRepository(
            FakeSettings(
                serverUrl = server.url("/").toString(),
                authToken = "test-token",
            )
        )
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    // ------------------------------------------------------------------
    // GET /api/settings
    // ------------------------------------------------------------------

    @Test
    fun `settings parse into the sections the hub renders`() = runTest {

        server.enqueue(ok(SETTINGS_BODY))

        val result = repository.loadSettings()

        assertEquals("/api/settings", server.takeRequest().path)

        assertTrue(result is AuraResult.Ok)

        val body = (result as AuraResult.Ok).value

        assertEquals("gemini", body.effective.llm.provider)
        assertEquals(0.75, body.effective.llm.temperature, 0.0001)
        assertEquals(listOf("groq", "mistral"), body.effective.llm.fallbackProviders)

        assertTrue(body.effective.memory.recall)
        assertEquals(20, body.effective.memory.historyLimit)

        // Off, and it has to stay off: a settings screen is exactly where
        // proactive could get quietly enabled.
        assertFalse(body.effective.proactive.enabled)
        assertEquals(listOf(listOf(22, 8)), body.effective.proactive.quietHours)

        assertEquals(12.5, body.effective.server.screen.minInterval, 0.0001)

        assertTrue(body.effective.tools.enabled)
        assertEquals(listOf("safe", "sensitive"), body.effective.tools.autoApprove)
        assertEquals(45.0, body.effective.tools.timeout, 0.0001)
        assertEquals(listOf("current_time"), body.effective.tools.allowed)
        assertEquals(mapOf("notepad" to "notepad.exe"), body.effective.tools.applications)
        // This body predates `commands` and does not carry it, which is the
        // point: an older server that never heard of `run_command` must not
        // make the settings screen fail to parse.
        assertTrue(body.effective.tools.commands.isEmpty())

        // The two path grants arrive separately and stay separate. Asserting
        // the values rather than the emptiness is the point: both fields
        // default to an empty list, so a misspelled @SerialName would pass
        // any check that only looked for empty. It also closes the same gap
        // for `allowed_paths`, whose value this body has always carried
        // without anything reading it back.
        assertEquals(listOf("D:\\AURA"), body.effective.tools.allowedPaths)
        assertEquals(listOf("D:\\notes"), body.effective.tools.writablePaths)

        assertEquals("edge", body.effective.voice.tts.provider)
        assertEquals("en-GB-SoniaNeural", body.effective.voice.tts.voice)
        assertEquals(80, body.effective.voice.tts.volume)
        assertFalse(body.effective.voice.tts.playback)
    }

    @Test
    fun `the configurable list is what the hub locks controls from`() = runTest {

        server.enqueue(ok(SETTINGS_BODY))

        val body = (repository.loadSettings() as AuraResult.Ok).value

        // Every path this phase added has to arrive intact, because a path
        // absent from this list renders as an unsupported control.
        assertTrue("tools.auto_approve" in body.configurable)
        assertTrue("voice.tts.volume" in body.configurable)
        assertTrue("server.screen.min_interval" in body.configurable)

        // And one the server deliberately does not accept.
        assertFalse("tools.allowed" in body.configurable)
    }

    @Test
    fun `a settings document from a sparser server still parses`() = runTest {

        // Every section omitted. An older deployment, or one that returns
        // a config this app has fields for and it does not.
        server.enqueue(ok("""{"effective": {}, "providers": {}}"""))

        val result = repository.loadSettings()

        assertTrue("a partial document must not fail the parse", result is AuraResult.Ok)

        val body = (result as AuraResult.Ok).value

        assertEquals("", body.effective.llm.provider)
        assertTrue(body.configurable.isEmpty())
        // A server from before phase 18.3 sends no `writable_paths`, and the
        // absence has to read as "nothing is writable" rather than as a
        // parse failure or, worse, as a permission.
        assertTrue(body.effective.tools.writablePaths.isEmpty())
        assertTrue(body.effective.tools.allowedPaths.isEmpty())
        // Not `true` by default: claiming keys are stored durably when the
        // server never said so is the wrong way to be wrong.
        assertFalse(body.providers.persistent)
    }

    @Test
    fun `an unknown field from a newer server is ignored`() = runTest {

        server.enqueue(
            ok("""{"effective": {}, "providers": {}, "invented_in_phase_12": 7}""")
        )

        assertTrue(repository.loadSettings() is AuraResult.Ok)
    }

    // ------------------------------------------------------------------
    // The document the deployed server actually sends
    //
    // Everything above is hand-written in the server's shape, which proves
    // the field names the app expects and nothing about the ones the server
    // sends. These three read `src/test/resources/live/*.json` - the exact
    // bodies `server/routes/settings.py` produced, whole and unedited, via
    // `tests/test_settings_fixture.py`. That test regenerates them and fails
    // if the server's shape moves, so this pair catches drift from either
    // side.
    // ------------------------------------------------------------------

    @Test
    fun `the deployed server's own settings document parses whole`() = runTest {

        server.enqueue(ok(liveBody("settings")))

        val result = repository.loadSettings()

        assertTrue("the live document must parse: $result", result is AuraResult.Ok)

        val body = (result as AuraResult.Ok).value

        // The app block, which is what the About row renders.
        assertEquals("Aura", body.effective.app.name)
        assertEquals("0.2.0", body.effective.app.version)

        // Every provider's model field, each read from its own key. A single
        // wrong `@SerialName` here is a model picker that silently edits the
        // wrong provider's setting.
        val llm = body.effective.llm
        assertEquals("gemini", llm.provider)
        assertEquals("gemini-3.6-flash", llm.model)
        assertEquals("gpt-5.1", llm.openaiModel)
        assertEquals("claude-sonnet-5", llm.anthropicModel)
        assertEquals("llama-3.3-70b", llm.cerebrasModel)
        assertEquals("grok-4", llm.xaiModel)
        assertEquals("deepseek-chat", llm.deepseekModel)
        assertEquals("qwen-plus", llm.qwenModel)
        assertEquals("llama-3.3-70b-versatile", llm.groqModel)
        assertEquals("mistral-small-latest", llm.mistralModel)
        assertEquals("qwen3:8b", llm.ollamaModel)
        assertEquals("openrouter/free", llm.fallbackModel)
        assertEquals(listOf("groq", "mistral", "openrouter"), llm.fallbackProviders)
        assertEquals(0.7, llm.temperature, 0.0001)
        assertEquals(768, llm.maxOutputTokens)
        assertEquals(120.0, llm.timeout, 0.0001)

        // Routing lanes. Blank on a stock server, and blank has to survive
        // the trip as blank: a lane read as anything else would show the
        // owner a preference they never expressed.
        assertEquals("", llm.taskModels.reasoning)
        assertEquals("", llm.taskModels.coding)
        assertEquals("", llm.taskModels.toolPlanning)
        assertEquals("", llm.taskModels.fastResponse)
        assertEquals("", llm.taskModels.longContext)

        // The custom endpoint arrives blank, and blank has to stay blank:
        // a placeholder rendered into the field would be an address the
        // owner never typed.
        assertEquals("", llm.customBaseUrl)
        assertEquals("", llm.customModel)

        assertEquals(10, body.effective.memory.historyLimit)
        assertEquals(500, body.effective.memory.retrievalScope)
        assertFalse(body.effective.memory.recall)

        assertFalse(body.effective.proactive.enabled)
        assertEquals(listOf(listOf(22, 8)), body.effective.proactive.quietHours)
        assertEquals(4, body.effective.proactive.maxPerDay)

        assertTrue(body.effective.server.screen.enabled)
        assertEquals(8.0, body.effective.server.screen.minInterval, 0.0001)
        assertFalse(body.effective.server.companion.enabled)

        assertTrue(body.effective.tools.enabled)
        assertEquals(listOf("safe"), body.effective.tools.autoApprove)

        // The shipped server declares no commands and does not allow
        // `run_command`. This is the phone's copy of that guarantee: if a
        // command ever appears in the live document it means the shipped
        // `config.yaml` enabled arbitrary program execution without anybody
        // deciding to, and the owner would find out from their phone.
        assertTrue(
            "the shipped server must declare no commands",
            body.effective.tools.commands.isEmpty(),
        )
        assertFalse("run_command" in body.effective.tools.allowed)

        // And no directory is writable. If one ever appears here it means
        // the shipped `config.yaml` handed out permission to overwrite the
        // owner's files without anybody deciding to - the same guarantee as
        // the commands check above, for the other half of section 24.
        assertTrue(
            "the shipped server must declare no writable directories",
            body.effective.tools.writablePaths.isEmpty(),
        )
        for (name in listOf(
            "write_file", "append_to_file", "create_directory", "delete_file",
        )) {
            assertFalse(name in body.effective.tools.allowed)
        }

        assertTrue(body.effective.vision.enabled)
        assertEquals("qwen2.5vl:7b", body.effective.vision.ollamaModel)
        assertFalse(body.effective.vision.captureScreen)
        // The shipped answer, and the one this assertion exists to keep:
        // the deployed server does not send its screen anywhere.
        assertFalse(body.effective.vision.sendScreenToCloud)
        assertEquals(2.0, body.effective.vision.minInterval, 0.0)

        assertEquals("auto", body.effective.voice.tts.provider)
        assertTrue(body.effective.voice.tts.playback)

        assertTrue(body.providers.persistent)
    }

    @Test
    fun `every path the deployed server calls configurable arrives intact`() = runTest {

        server.enqueue(ok(liveBody("settings")))

        val configurable = (repository.loadSettings() as AuraResult.Ok).value.configurable

        // The whole allow-list, not a sample: a path lost in transit renders
        // as a control this server "does not support".
        assertEquals(59, configurable.size)

        listOf(
            "llm.provider", "llm.model", "llm.anthropic_model", "llm.qwen_model",
            "llm.temperature", "llm.max_output_tokens", "llm.timeout",
            // One path per routing lane. Named individually rather than
            // merely counted: a lane the server stopped offering is an owner
            // who can no longer retire that lane from the phone.
            "llm.task_models.reasoning", "llm.task_models.coding",
            "llm.task_models.tool_planning", "llm.task_models.fast_response",
            "llm.task_models.long_context",
            // The custom endpoint. Unlike every other provider's settings
            // these are not optional polish: without them `custom` cannot
            // be built at all, so a path lost here is a provider the owner
            // can select and never configure.
            "llm.custom_base_url", "llm.custom_model",
            "memory.recall", "memory.history_limit",
            "proactive.enabled", "proactive.quiet_hours",
            "server.screen.enabled", "server.screen.min_interval",
            // Every knob on the companion gate, not just its switch. The
            // gate is what decides whether Aura speaks first; an owner who
            // finds her chatty and can only reach `enabled` has one
            // remedy, silence, and nothing between that and the default.
            "server.companion.enabled",
            "server.companion.relevance_threshold",
            "server.companion.cooldown_seconds",
            "server.companion.max_per_hour",
            "server.companion.quiet_hours",
            "server.companion.suppress_after_chat_seconds",
            "server.companion.duplicate_window_seconds",
            "tools.enabled", "tools.auto_approve", "tools.timeout",
            "vision.enabled",
            // Both halves of screen awareness, not just the outer switch.
            // These two were readable in `effective` and settable nowhere
            // until phase 19: the owner could change them by editing a
            // file on the server and not from the app that is supposed to
            // configure it.
            "vision.capture_screen", "vision.min_interval",
            // The one path in this list that decides whether a picture of
            // the owner's desktop leaves their machine. Pinned so it can
            // never become un-settable: an owner who can turn this on and
            // then finds no control to turn it off has lost the switch
            // that matters most.
            "vision.send_screen_to_cloud",
            "voice.tts.enabled", "voice.tts.volume",
            // The zone the server dates everything in. `effective` has
            // always carried this value, so a path lost here is a phone
            // that displays the wrong time and cannot correct it.
            "temporal.timezone",
        ).forEach {
            assertTrue("$it must be configurable", it in configurable)
        }

        // And the capability-granting ones still are not. A bearer token
        // must not be able to widen what the tools may touch.
        assertFalse("tools.allowed" in configurable)
        assertFalse("tools.allowed_paths" in configurable)
        assertFalse("tools.applications" in configurable)

        // `commands` most of all: a settable one would let anything holding
        // the token declare `["cmd", "/c", "{x}"]` and then ask
        // `run_command` to fill in `{x}`, which is arbitrary shell execution
        // arrived at through the settings API instead of through the tool
        // boundary. The prefix check catches a future server that starts
        // sending them one at a time as `tools.commands.<name>`.
        assertFalse("tools.commands" in configurable)
        assertTrue(configurable.none { it.startsWith("tools.commands") })

        // `writable_paths` for the same reason: a settable one would let
        // anything holding the token add `C:/` and then ask `write_file` to
        // replace whatever it liked - filesystem access reached around the
        // tool boundary through the settings API instead of through it.
        assertFalse("tools.writable_paths" in configurable)
        assertTrue(configurable.none { it.startsWith("tools.writable_paths") })
    }

    @Test
    fun `nulls in sections this app does not declare are dropped, not fatal`() = runTest {

        // The live document contains `avatar.position: null` and
        // `voice.microphone.device: null`. Neither section is declared here,
        // so `ignoreUnknownKeys` drops them before any converter sees a null
        // - which is the reason the live 200 parses without
        // `coerceInputValues`, and worth pinning rather than rediscovering.
        server.enqueue(
            ok(
                """
                {"effective": {"avatar": {"position": null, "opacity": 0.95},
                 "voice": {"microphone": {"device": null},
                 "tts": {"provider": "edge"}}},
                 "providers": {"persistent": true}}
                """
            )
        )

        val result = repository.loadSettings()

        assertTrue("nulls outside the DTOs must not fail the parse", result is AuraResult.Ok)
        assertEquals("edge", (result as AuraResult.Ok).value.effective.voice.tts.provider)
    }

    @Test
    fun `a null where a value is declared is incompatible, not a crash`() = runTest {

        // The opposite direction, and the one that would be a real version
        // mismatch: no `coerceInputValues`, so a declared field arriving null
        // fails the parse. It has to read as "this app could not read the
        // answer", never as a missing endpoint.
        server.enqueue(ok("""{"effective": {"llm": {"provider": null}}, "providers": {}}"""))

        val error = (repository.loadSettings() as AuraResult.Failed).error

        assertTrue("expected Incompatible, got $error", error is AuraError.Incompatible)
        assertFalse(error is AuraError.NotSupported)
    }

    // ------------------------------------------------------------------
    // PATCH /api/settings
    // ------------------------------------------------------------------

    @Test
    fun `a patch nests dotted paths the way the server merges them`() = runTest {

        server.enqueue(
            ok(
                """
                {"applied": ["tools.timeout"], "restart_required": [],
                 "persistent": true, "needs_restart": false, "effective": {}}
                """
            )
        )

        val result = repository.patchSettings(
            mapOf("tools.timeout" to JsonPrimitive(45))
        )

        val request = server.takeRequest()

        assertEquals("PATCH", request.method)
        assertEquals("/api/settings", request.path)

        val body = json.parseToJsonElement(request.body.readUtf8()) as JsonObject

        // `{"settings": {"tools": {"timeout": 45}}}` - nested, not the
        // dotted key as a literal field name, which is what the server's
        // `SettingsPatch` model would flatten differently.
        val tools = body["settings"]!!.jsonObject["tools"]!!.jsonObject
        assertEquals(45, tools["timeout"]!!.jsonPrimitive.int)

        assertTrue(result is AuraResult.Ok)
        assertEquals(listOf("tools.timeout"), (result as AuraResult.Ok).value.applied)
    }

    @Test
    fun `a list setting is sent as a JSON array`() = runTest {

        server.enqueue(
            ok(
                """
                {"applied": ["tools.auto_approve"], "restart_required": [],
                 "persistent": true, "needs_restart": false, "effective": {}}
                """
            )
        )

        repository.patchSettings(
            mapOf(
                "tools.auto_approve" to json.parseToJsonElement("""["safe","sensitive"]"""),
            )
        )

        val body = json.parseToJsonElement(
            server.takeRequest().body.readUtf8()
        ) as JsonObject

        val approved = body["settings"]!!
            .jsonObject["tools"]!!
            .jsonObject["auto_approve"]!!
            .jsonArray

        assertEquals(2, approved.size)
        assertEquals("safe", approved[0].jsonPrimitive.content)
    }

    @Test
    fun `a nested path lands two levels deep`() = runTest {

        server.enqueue(
            ok(
                """
                {"applied": [], "restart_required": ["voice.tts.provider"],
                 "persistent": true, "needs_restart": true, "effective": {}}
                """
            )
        )

        val result = repository.patchSettings(
            mapOf("voice.tts.provider" to JsonPrimitive("edge"))
        )

        val body = json.parseToJsonElement(
            server.takeRequest().body.readUtf8()
        ) as JsonObject

        assertEquals(
            "edge",
            body["settings"]!!
                .jsonObject["voice"]!!
                .jsonObject["tts"]!!
                .jsonObject["provider"]!!
                .jsonPrimitive.content,
        )

        // The report the UI shows as "needs a restart" rather than "done".
        val patched = (result as AuraResult.Ok).value
        assertTrue(patched.applied.isEmpty())
        assertEquals(listOf("voice.tts.provider"), patched.restartRequired)
        assertTrue(patched.needsRestart)
    }

    @Test
    fun `a patch answers with the server's own effective document`() = runTest {

        // The server clamped what was sent: 0.9 asked for, 0.7 in effect.
        // Whoever renders this has to take the second number - a screen that
        // keeps showing the value it sent is a screen that lies about what
        // Aura is running.
        server.enqueue(
            ok(
                """
                {"applied": ["llm.temperature"], "restart_required": [],
                 "persistent": true, "needs_restart": false,
                 "effective": {"llm": {"provider": "gemini", "temperature": 0.7}}}
                """
            )
        )

        val result = repository.patchSettings(
            mapOf("llm.temperature" to JsonPrimitive(0.9))
        )

        val sent = json.parseToJsonElement(server.takeRequest().body.readUtf8()) as JsonObject

        assertEquals(
            0.9,
            sent["settings"]!!.jsonObject["llm"]!!
                .jsonObject["temperature"]!!.jsonPrimitive.double,
            0.0001,
        )

        val report = (result as AuraResult.Ok).value

        assertEquals(listOf("llm.temperature"), report.applied)
        assertEquals(0.7, report.effective.llm.temperature, 0.0001)
    }

    @Test
    fun `an empty patch never reaches the network`() = runTest {

        val result = repository.patchSettings(emptyMap())

        assertTrue(result is AuraResult.Failed)
        assertEquals(0, server.requestCount)
    }

    @Test
    fun `a rejected setting keeps the server's own wording`() = runTest {

        // `HTTPException(422, detail={"error": ..., "message": ...})`, and
        // the message comes from `core/settings_store.py` - written for a
        // person, naming the offending path, containing no exception text.
        // Far more useful than "unexpected response (422)".
        server.enqueue(
            MockResponse()
                .setResponseCode(422)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """
                    {"detail": {"error": "invalid_setting",
                     "message": "tools.timeout must be between 1.0 and 300.0"}}
                    """.trimIndent()
                )
        )

        val result = repository.patchSettings(
            mapOf("tools.timeout" to JsonPrimitive(9000))
        )

        assertTrue(result is AuraResult.Failed)

        val error = (result as AuraResult.Failed).error

        assertTrue(error is AuraError.Rejected)
        assertTrue(error.userMessage.contains("tools.timeout"))
    }

    @Test
    fun `pydantic's own 422 shape does not reach the screen`() = runTest {

        // FastAPI's request validation returns 422 as a *list* of error
        // objects full of `loc`/`type`/`ctx` internals. Useless on a phone,
        // so it becomes a generic refusal rather than being shown.
        server.enqueue(
            MockResponse()
                .setResponseCode(422)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """
                    {"detail": [{"loc": ["body", "settings"], "msg": "field required",
                     "type": "value_error.missing"}]}
                    """.trimIndent()
                )
        )

        val result = repository.patchSettings(
            mapOf("tools.timeout" to JsonPrimitive(45))
        )

        val error = (result as AuraResult.Failed).error

        assertTrue(error is AuraError.Rejected)
        assertFalse(error.userMessage.contains("value_error"))
        assertFalse(error.userMessage.contains("loc"))
    }

    // ------------------------------------------------------------------
    // The two failures that caused this phase
    // ------------------------------------------------------------------

    @Test
    fun `a 404 from settings is a missing feature, not a dead server`() = runTest {

        server.enqueue(MockResponse().setResponseCode(404))

        val result = repository.loadSettings()

        assertTrue(result is AuraResult.Failed)

        // NotSupported, not ServerFailure: the hub branches on this to say
        // "connected, settings unavailable" instead of "disconnected". A
        // deployment older than the app answers /api/chat perfectly.
        assertEquals(
            AuraError.NotSupported,
            (result as AuraResult.Failed).error,
        )
    }

    @Test
    fun `a 404 from providers and provider health reads the same way`() = runTest {

        server.enqueue(MockResponse().setResponseCode(404))
        server.enqueue(MockResponse().setResponseCode(404))

        val providers = repository.loadProviders()
        val health = repository.providerHealth()

        assertEquals(
            AuraError.NotSupported,
            (providers as AuraResult.Failed).error,
        )
        assertEquals(
            AuraError.NotSupported,
            (health as AuraResult.Failed).error,
        )
    }

    @Test
    fun `a 401 is a token problem and says so`() = runTest {

        server.enqueue(MockResponse().setResponseCode(401))

        val result = repository.loadSettings()

        assertEquals(
            AuraError.Unauthorized,
            (result as AuraResult.Failed).error,
        )
        assertTrue(
            AuraError.Unauthorized.userMessage.contains("token"),
        )
    }

    @Test
    fun `a 403 is a permission problem, not a wrong token`() = runTest {

        server.enqueue(MockResponse().setResponseCode(403))

        // Split from 401 in this phase. Both are about the token; only one of
        // them is fixed by replacing it, and the hub renders the two
        // differently because the actions differ.
        assertEquals(
            AuraError.Forbidden,
            (repository.loadSettings() as AuraResult.Failed).error,
        )
    }

    @Test
    fun `a 429 is a rate limit, not a missing feature`() = runTest {

        server.enqueue(MockResponse().setResponseCode(429))

        val error = (repository.loadSettings() as AuraResult.Failed).error

        assertEquals(AuraError.RateLimited, error)

        // It used to arrive as ServerFailure(429), which the hub then rendered
        // as "this Aura server does not expose settings" - about a server that
        // has the endpoint and was merely being asked too often.
        assertTrue(error !is AuraError.NotSupported)
        assertFalse(error.userMessage.contains("429"))
    }

    @Test
    fun `a 500 carries a code and never a body`() = runTest {

        server.enqueue(
            MockResponse()
                .setResponseCode(500)
                .setBody("Traceback (most recent call last): AURA_SECRET_KEY=hunter2")
        )

        val result = repository.loadSettings()

        val error = (result as AuraResult.Failed).error

        assertTrue(error is AuraError.ServerFailure)
        assertEquals(500, (error as AuraError.ServerFailure).code)

        // Whatever the server leaked, the user sees a sentence.
        assertFalse(error.userMessage.contains("hunter2"))
        assertFalse(error.userMessage.contains("Traceback"))
    }

    @Test
    fun `a malformed body is a failure, not a crash`() = runTest {

        server.enqueue(ok("{ this is not json"))

        assertTrue(repository.loadSettings() is AuraResult.Failed)
    }

    @Test
    fun `a body this app cannot read is incompatible, not absent`() = runTest {

        // A 200 whose body will not deserialize: a truncated response, or a
        // proxy's HTML error page served with a success code. The route
        // answered, so the one thing this must not become is NotSupported -
        // the hub renders that as "this server does not expose settings".
        server.enqueue(ok("{ this is not json"))

        val error = (repository.loadSettings() as AuraResult.Failed).error

        assertTrue("expected Incompatible, got $error", error is AuraError.Incompatible)
        assertTrue(error !is AuraError.NotSupported)

        // The parse exception quotes the JSON it choked on, which can carry
        // configuration. None of it reaches the screen.
        assertFalse(error.userMessage.contains("this is not json"))
        assertFalse(error.userMessage.contains("JsonDecodingException"))
    }

    @Test
    fun `an HTML error page served as a success is incompatible`() = runTest {

        // What a misconfigured proxy in front of Aura actually returns.
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("<html><body>502 Bad Gateway</body></html>")
        )

        val error = (repository.loadSettings() as AuraResult.Failed).error

        assertTrue("expected Incompatible, got $error", error is AuraError.Incompatible)
        assertFalse(error.userMessage.contains("html"))
    }

    @Test
    fun `an empty successful body never reports a success code as a failure`() = runTest {

        // Retrofit hands back a null body here, which used to fall through to
        // `else -> ServerFailure(code)` and produce the sentence "Aura
        // returned an unexpected response (200)" - a success code named as the
        // fault.
        server.enqueue(MockResponse().setResponseCode(204))

        val error = (repository.loadSettings() as AuraResult.Failed).error

        assertTrue("expected Incompatible, got $error", error is AuraError.Incompatible)
        assertFalse(error.userMessage.contains("200"))
        assertFalse(error.userMessage.contains("204"))
        assertFalse(error.userMessage.contains("unexpected response"))
    }

    // ------------------------------------------------------------------
    // GET /api/providers
    // ------------------------------------------------------------------

    @Test
    fun `providers parse with their capabilities and key state`() = runTest {

        server.enqueue(ok(PROVIDERS_BODY))

        val result = repository.loadProviders()

        assertEquals("/api/providers", server.takeRequest().path)

        val body = (result as AuraResult.Ok).value

        assertEquals("gemini", body.primary)
        assertEquals(listOf("groq"), body.fallbackProviders)
        assertEquals("gemini->groq", body.activeChain)
        assertTrue(body.keyStorage.persistent)

        val gemini = body.providers.first { it.name == "gemini" }

        assertEquals("Google Gemini", gemini.label)
        assertTrue(gemini.vision)
        assertFalse(gemini.keyless)
        assertTrue(gemini.configured)
        assertTrue(gemini.isPrimary)
        assertFalse(gemini.isFallback)

        val ollama = body.providers.first { it.name == "ollama" }

        // Keyless: configured with no key stored, which must not render as
        // "not set up".
        assertTrue(ollama.keyless)
        assertTrue(ollama.configured)
        assertEquals("", ollama.keyMasked)
    }

    @Test
    fun `the deployed server names the setting each provider's model lives in`() = runTest {

        server.enqueue(ok(liveBody("providers")))

        val body = (repository.loadProviders() as AuraResult.Ok).value

        // The mapping the model picker writes through. Every one of these is
        // the server's answer, not a table the phone carries: writing
        // `llm.model` for Anthropic saves a name only Gemini ever reads.
        val expected = mapOf(
            "gemini" to "llm.model",
            "openai" to "llm.openai_model",
            "anthropic" to "llm.anthropic_model",
            "groq" to "llm.groq_model",
            "cerebras" to "llm.cerebras_model",
            "openrouter" to "llm.fallback_model",
            "mistral" to "llm.mistral_model",
            "xai" to "llm.xai_model",
            "deepseek" to "llm.deepseek_model",
            "qwen" to "llm.qwen_model",
            "ollama" to "llm.ollama_model",
        )

        expected.forEach { (name, setting) ->

            val provider = body.providers.first { it.name == name }

            assertEquals(name, setting, provider.modelSetting)

            // And the fallback never fires for a server that reports one, so
            // no provider but Gemini can be sent to `llm.model`.
            assertEquals(setting, provider.modelSettingOr())
        }

        // `mock` has no model at all, and is the one case that falls through.
        val mock = body.providers.first { it.name == "mock" }
        assertEquals("", mock.modelSetting)
        assertEquals("llm.model", mock.modelSettingOr())
    }

    @Test
    fun `each provider reports the model it would be built with`() = runTest {

        server.enqueue(ok(liveBody("providers")))

        val body = (repository.loadProviders() as AuraResult.Ok).value

        assertEquals(
            "claude-sonnet-5",
            body.providers.first { it.name == "anthropic" }.model,
        )
        assertEquals(
            "grok-4",
            body.providers.first { it.name == "xai" }.model,
        )

        // Not Gemini's, which is what a UI reading `llm.model` for everyone
        // would have shown.
        assertFalse(
            body.providers
                .filter { it.name != "gemini" }
                .any { it.model == "gemini-3.6-flash" },
        )
    }

    @Test
    fun `the deployed providers document names variables and never their values`() = runTest {

        val raw = liveBody("providers")

        server.enqueue(ok(raw))

        val body = (repository.loadProviders() as AuraResult.Ok).value

        val openai = body.providers.first { it.name == "openai" }

        assertEquals("OPENAI_API_KEY", openai.apiKeyEnv)
        assertEquals("https://api.openai.com/v1", openai.apiBase)
        assertFalse(openai.apiBaseOverridden)

        // The variable's name is in the payload; nothing key-shaped is.
        listOf("AIza", "gsk_", "sk-", "xai-", "csk-").forEach {
            assertFalse("the providers document must not carry $it", raw.contains(it))
        }
    }

    @Test
    fun `a stored key arrives masked and only masked`() = runTest {
        server.enqueue(ok(PROVIDERS_BODY))

        val body = (repository.loadProviders() as AuraResult.Ok).value

        val gemini = body.providers.first { it.name == "gemini" }

        assertEquals("••••••••ABCD", gemini.keyMasked)
        assertEquals("store", gemini.keySource)

        // The masked form is the only key-shaped thing in the payload. If a
        // GET ever started returning the real value this fails here first.
        assertFalse(PROVIDERS_BODY.contains("AIza"))
        assertFalse(PROVIDERS_BODY.contains("gsk_"))
    }

    @Test
    fun `an unconfigured provider has an empty mask, not a placeholder`() = runTest {

        server.enqueue(ok(PROVIDERS_BODY))

        val body = (repository.loadProviders() as AuraResult.Ok).value

        val mistral = body.providers.first { it.name == "mistral" }

        assertFalse(mistral.configured)
        assertEquals("", mistral.keyMasked)
        assertEquals("", mistral.keySource)
    }

    @Test
    fun `setting a key sends it in the body and gets back only a mask`() = runTest {

        server.enqueue(
            ok("""{"provider": "groq", "saved": true, "persistent": true, "key_masked": "••••••••WXYZ"}""")
        )

        val result = repository.setProviderKey("groq", "gsk_a-real-looking-key-WXYZ")

        val request = server.takeRequest()

        assertEquals("PUT", request.method)
        assertEquals("/api/providers/groq/key", request.path)

        // The key goes up in the body, never in the path or a query string,
        // where it would land in the server's access log.
        val sent = json.parseToJsonElement(request.body.readUtf8()) as JsonObject
        assertEquals(
            "gsk_a-real-looking-key-WXYZ",
            sent["key"]!!.jsonPrimitive.content,
        )

        val body = (result as AuraResult.Ok).value

        assertTrue(body.saved)
        assertTrue(body.persistent)
        assertEquals("••••••••WXYZ", body.keyMasked)
        assertEquals("", body.warning)
    }

    @Test
    fun `a key the server cannot store durably still reports success`() = runTest {

        // No AURA_SECRET_KEY on the server: the key works for this process
        // and dies at restart. Saved, with the caveat carried in `warning`
        // so the UI can say so instead of reporting a failure.
        server.enqueue(
            ok(
                """
                {"provider": "groq", "saved": true, "persistent": false,
                 "key_masked": "••••••••WXYZ",
                 "warning": "Set AURA_SECRET_KEY on the server to store provider keys encrypted at rest."}
                """
            )
        )

        val body = (repository.setProviderKey("groq", "gsk_x") as AuraResult.Ok).value

        assertTrue(body.saved)
        assertFalse(body.persistent)
        assertTrue(body.warning.isNotBlank())
        // The caveat names the environment variable, not its value.
        assertFalse(body.warning.contains("gsk_"))
    }

    @Test
    fun `a masked value sent back as a key is refused`() = runTest {

        // The server refuses to store bullets - which would look like
        // success and quietly break the provider.
        server.enqueue(
            MockResponse()
                .setResponseCode(422)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"detail": {"error": "invalid_key", "message": "That looks like a masked value, not a key."}}"""
                )
        )

        val result = repository.setProviderKey("groq", "••••••••WXYZ")

        assertTrue(result is AuraResult.Failed)

        val error = (result as AuraResult.Failed).error

        assertTrue(error is AuraError.Rejected)
        assertTrue(error.userMessage.contains("masked"))
    }

    @Test
    fun `deleting a key reports whether there was one`() = runTest {

        server.enqueue(
            ok("""{"provider": "groq", "deleted": true, "key_masked": ""}""")
        )

        val result = repository.deleteProviderKey("groq")

        val request = server.takeRequest()

        assertEquals("DELETE", request.method)
        assertEquals("/api/providers/groq/key", request.path)

        val body = (result as AuraResult.Ok).value

        assertTrue(body.deleted)
        assertEquals("", body.keyMasked)
    }

    // ------------------------------------------------------------------
    // GET /api/providers/health
    // ------------------------------------------------------------------

    @Test
    fun `provider health parses the chain and the per-provider map`() = runTest {

        server.enqueue(ok(HEALTH_BODY))

        val result = repository.providerHealth()

        assertEquals("/api/providers/health", server.takeRequest().path)

        val body = (result as AuraResult.Ok).value

        assertEquals("gemini", body.requested)
        assertEquals("groq", body.active)
        assertEquals(listOf("gemini", "groq", "mistral"), body.chain)
        assertTrue(body.inFallback)
        assertTrue(body.ready)

        assertEquals(6, body.providers.size)

        // The five states, each with the meaning the UI writes a subtitle
        // from. `healthy` is "currently serving", not "answered a probe".
        assertEquals("failed", body.providers["gemini"]!!.state)
        assertFalse(body.providers["gemini"]!!.healthy)

        assertEquals("active", body.providers["groq"]!!.state)
        assertTrue(body.providers["groq"]!!.healthy)
        assertTrue(body.providers["groq"]!!.inChain)

        assertEquals("standby", body.providers["mistral"]!!.state)
        assertEquals("idle", body.providers["openrouter"]!!.state)

        assertEquals("unconfigured", body.providers["mock"]!!.state)
        assertFalse(body.providers["mock"]!!.configured)
    }

    @Test
    fun `one unreadable provider does not take the map with it`() = runTest {

        server.enqueue(ok(HEALTH_BODY))

        val body = (repository.providerHealth() as AuraResult.Ok).value

        val broken = body.providers["ollama"]!!

        assertEquals("error", broken.state)
        assertFalse(broken.healthy)

        // A category, not a message. Anything longer than a class name here
        // would mean the server started forwarding exception text to a phone.
        assertEquals("RuntimeError", broken.problem)

        // And the rest of the map survived.
        assertEquals("active", body.providers["groq"]!!.state)
    }

    @Test
    fun `a server without the per-provider map still answers usefully`() = runTest {

        // The chain-level fields are what the recovery UI needs; the map
        // arrived later. Its absence renders as "not reported", it does not
        // fail the parse.
        server.enqueue(
            ok(
                """
                {"requested": "gemini", "active": "gemini",
                 "chain": ["gemini"], "in_fallback": false,
                 "problems": [], "ready": true}
                """
            )
        )

        val body = (repository.providerHealth() as AuraResult.Ok).value

        assertTrue(body.providers.isEmpty())
        assertTrue(body.ready)
        assertEquals(listOf("gemini"), body.chain)
    }

    @Test
    fun `a chain that could not be read reports a problem and is not ready`() = runTest {

        server.enqueue(
            ok(
                """
                {"requested": "gemini", "active": "", "chain": [],
                 "in_fallback": false, "ready": false,
                 "problems": ["provider chain unavailable (AttributeError)"],
                 "providers": {}}
                """
            )
        )

        val body = (repository.providerHealth() as AuraResult.Ok).value

        assertFalse(body.ready)
        assertEquals(1, body.problems.size)
        // A class name in parentheses, which is all the server sends.
        assertTrue(body.problems.first().contains("AttributeError"))
    }

    @Test
    fun `the deployed server's own health document parses whole`() = runTest {

        server.enqueue(ok(liveBody("provider_health")))

        val body = (repository.providerHealth() as AuraResult.Ok).value

        assertEquals("/api/providers/health", server.takeRequest().path)

        // A deployment with no keys: it asked for Gemini, built nothing, and
        // says so. Every provider the build knows about is still reported, so
        // the section can show a row for each rather than an empty list.
        assertEquals("gemini", body.requested)
        assertEquals("", body.active)
        assertFalse(body.ready)
        assertFalse(body.inFallback)
        assertEquals(13, body.providers.size)
        assertEquals("unconfigured", body.providers.getValue("custom").state)

        val gemini = body.providers.getValue("gemini")
        assertFalse(gemini.configured)
        assertEquals("unconfigured", gemini.state)

        // Keyless providers are configured without a key, and idle rather
        // than unconfigured - the distinction the recovery UI renders.
        val ollama = body.providers.getValue("ollama")
        assertTrue(ollama.configured)
        assertEquals("idle", ollama.state)

        // The problem is a category with an exception class in it, and
        // nothing else. Not a traceback, not a path, not a key.
        assertEquals(1, body.problems.size)
        assertFalse(body.problems.first().contains("/"))
    }

    // ------------------------------------------------------------------
    // Authentication, on every one of these routes
    // ------------------------------------------------------------------

    @Test
    fun `every settings route carries the bearer token`() = runTest {

        server.enqueue(ok(SETTINGS_BODY))
        server.enqueue(ok(PROVIDERS_BODY))
        server.enqueue(ok(HEALTH_BODY))

        repository.loadSettings()
        repository.loadProviders()
        repository.providerHealth()

        repeat(3) {
            assertEquals(
                "Bearer test-token",
                server.takeRequest().getHeader("Authorization"),
            )
        }
    }

    @Test
    fun `no server address means no request at all`() = runTest {

        val unconfigured = AuraRepository(FakeSettings(serverUrl = "", authToken = ""))

        val result = unconfigured.loadSettings()

        assertEquals(
            AuraError.NotConfigured,
            (result as AuraResult.Failed).error,
        )
    }

    // ------------------------------------------------------------------
    // Payloads, in the server's exact shape
    // ------------------------------------------------------------------

    private fun ok(body: String) = MockResponse()
        .setResponseCode(200)
        .setHeader("Content-Type", "application/json")
        .setBody(body.trimIndent())

    /**
     * One of the bodies the server itself produced.
     *
     * A resource rather than a string constant, so it is the server's output
     * byte for byte - a payload retyped into a Kotlin literal is a payload
     * someone has already interpreted. `tests/test_settings_fixture.py`
     * writes these and fails when they no longer match the routes.
     */
    private fun liveBody(name: String): String =
        checkNotNull(javaClass.getResourceAsStream("/live/$name.json")) {
            "missing test resource /live/$name.json - run tests/test_settings_fixture.py"
        }.use { it.readBytes().toString(Charsets.UTF_8) }

    private companion object {

        /**
         * `GET /api/settings`, trimmed to the sections with a control on
         * them. Field names are the server's, snake_case included.
         */
        val SETTINGS_BODY = """
            {
              "effective": {
                "app": {"name": "Aura", "version": "0.2.0"},
                "llm": {
                  "provider": "gemini",
                  "model": "gemini-3.6-flash",
                  "fallback_providers": ["groq", "mistral"],
                  "fallback_model": "llama-3.3-70b-versatile",
                  "temperature": 0.75,
                  "max_output_tokens": 768,
                  "timeout": 45.0
                },
                "memory": {
                  "recall": true, "profile": true, "pipeline": true,
                  "history_limit": 20, "retrieval_scope": 500
                },
                "proactive": {
                  "enabled": false,
                  "cooldown_seconds": 7200.0,
                  "max_per_day": 4,
                  "quiet_hours": [[22, 8]],
                  "duplicate_window_seconds": 21600.0,
                  "similarity_threshold": 0.6
                },
                "vision": {"enabled": false, "cloud_model": "", "ollama_model": "llava"},
                "voice": {
                  "tts": {
                    "enabled": true, "provider": "edge",
                    "voice": "en-GB-SoniaNeural", "volume": 80,
                    "playback": false
                  },
                  "stt": {"enabled": false, "provider": "whisper"}
                },
                "tools": {
                  "enabled": true,
                  "allowed": ["current_time"],
                  "auto_approve": ["safe", "sensitive"],
                  "timeout": 45.0,
                  "allowed_paths": ["D:\\AURA"],
                  "writable_paths": ["D:\\notes"],
                  "applications": {"notepad": "notepad.exe"}
                },
                "server": {
                  "screen": {"enabled": true, "min_interval": 12.5},
                  "companion": {"enabled": true}
                }
              },
              "providers": {"persistent": true, "persistence_note": ""},
              "configurable": [
                "llm.provider", "memory.recall", "proactive.enabled",
                "server.screen.min_interval", "tools.auto_approve",
                "tools.enabled", "tools.timeout", "voice.tts.playback",
                "voice.tts.provider", "voice.tts.voice", "voice.tts.volume"
              ]
            }
        """

        val PROVIDERS_BODY = """
            {
              "providers": [
                {
                  "name": "gemini", "label": "Google Gemini",
                  "chat": true, "streaming": true, "tools": true, "vision": true,
                  "keyless": false, "models": ["gemini-3.6-flash"],
                  "configured": true, "key_masked": "••••••••ABCD",
                  "key_source": "store", "is_primary": true, "is_fallback": false
                },
                {
                  "name": "groq", "label": "Groq",
                  "chat": true, "streaming": false, "tools": true, "vision": false,
                  "keyless": false, "models": ["llama-3.3-70b-versatile"],
                  "configured": true, "key_masked": "••••••••WXYZ",
                  "key_source": "environment", "is_primary": false, "is_fallback": true
                },
                {
                  "name": "mistral", "label": "Mistral",
                  "chat": true, "streaming": true, "tools": true, "vision": false,
                  "keyless": false, "models": [],
                  "configured": false, "key_masked": "", "key_source": "",
                  "is_primary": false, "is_fallback": false
                },
                {
                  "name": "ollama", "label": "Ollama (local)",
                  "chat": true, "streaming": true, "tools": true, "vision": false,
                  "keyless": true, "models": [],
                  "configured": true, "key_masked": "", "key_source": "",
                  "is_primary": false, "is_fallback": false
                }
              ],
              "primary": "gemini",
              "fallback_providers": ["groq"],
              "active_chain": "gemini->groq",
              "key_storage": {"persistent": true, "persistence_note": ""}
            }
        """

        /** Every state at once, including the isolated failure. */
        val HEALTH_BODY = """
            {
              "requested": "gemini",
              "active": "groq",
              "chain": ["gemini", "groq", "mistral"],
              "in_fallback": true,
              "problems": [],
              "ready": true,
              "providers": {
                "gemini": {"configured": true, "healthy": false, "state": "failed", "in_chain": true},
                "groq": {"configured": true, "healthy": true, "state": "active", "in_chain": true},
                "mistral": {"configured": true, "healthy": false, "state": "standby", "in_chain": true},
                "openrouter": {"configured": true, "healthy": false, "state": "idle", "in_chain": false},
                "ollama": {"configured": false, "healthy": false, "state": "error", "in_chain": false, "problem": "RuntimeError"},
                "mock": {"configured": false, "healthy": false, "state": "unconfigured", "in_chain": false}
              }
            }
        """
    }
}
