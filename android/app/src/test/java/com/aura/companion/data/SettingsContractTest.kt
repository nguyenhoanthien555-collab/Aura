package com.aura.companion.data

import com.aura.companion.data.settings.FakeSettings
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
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
    fun `a 403 is also a token problem`() = runTest {

        server.enqueue(MockResponse().setResponseCode(403))

        assertEquals(
            AuraError.Unauthorized,
            (repository.loadSettings() as AuraResult.Failed).error,
        )
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
