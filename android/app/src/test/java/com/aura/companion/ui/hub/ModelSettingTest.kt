package com.aura.companion.ui.hub

import com.aura.companion.data.remote.EffectiveConfigDto
import com.aura.companion.data.remote.LlmConfigDto
import com.aura.companion.data.remote.ProviderDto
import com.aura.companion.data.settings.AuraSettings
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Which setting a chosen model is written to.
 *
 * THE BUG THIS EXISTS FOR
 * -----------------------
 * The model picker wrote `llm.model` for every provider. `brain/router.py`
 * reads `llm.model` only for Gemini: Anthropic's model lives at
 * `llm.anthropic_model`, Qwen's at `llm.qwen_model`, OpenRouter's at
 * `llm.fallback_model`, and so on for all eleven. So choosing
 * `claude-sonnet-5` while Claude was primary saved a string that only Gemini
 * would ever read, the PATCH returned 200, the row showed the new value, and
 * Aura kept using the old model. A control that appears to work and cannot is
 * worse than one that is greyed out - rule 7 of the mandate says not to build
 * fake controls, and this was one.
 *
 * The fix is that the server reports `model_setting` per provider and the app
 * writes wherever it is told. These tests pin that the app does not go back
 * to guessing.
 */
class ModelSettingTest {

    @Test
    fun `the model setting follows the primary provider`() {

        assertEquals(
            "llm.anthropic_model",
            state(
                primary = "anthropic",
                providers = listOf(
                    provider("gemini", modelSetting = "llm.model"),
                    provider("anthropic", modelSetting = "llm.anthropic_model"),
                ),
            ).modelSetting,
        )
    }

    @Test
    fun `every provider the server reports gets its own setting`() {

        // Whatever the server says, verbatim. The app carries no copy of this
        // table, which is the only reason it cannot drift from the router.
        val table = mapOf(
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

        val providers = table.map { (name, setting) ->
            provider(name, modelSetting = setting)
        }

        table.forEach { (name, expected) ->
            assertEquals(
                "$name should write $expected",
                expected,
                state(primary = name, providers = providers).modelSetting,
            )
        }
    }

    @Test
    fun `an older server that reports no mapping keeps the old behaviour`() {

        // `model_setting` did not exist before this phase. On a deployment
        // without it, `llm.model` is the correct answer and was the only one
        // the app ever used - so nothing regresses, rather than the picker
        // silently doing nothing.
        assertEquals(
            "llm.model",
            state(
                primary = "gemini",
                providers = listOf(provider("gemini", modelSetting = "")),
            ).modelSetting,
        )
    }

    @Test
    fun `a provider the server never listed still writes somewhere real`() {

        // The providers route 404d, or named a provider this build does not
        // list. `llm.model` is a settable path on every server that has the
        // settings API at all, so the picker degrades instead of 422ing.
        assertEquals("llm.model", state(primary = "openai").modelSetting)
        assertEquals("llm.model", HubUiState().modelSetting)
    }

    @Test
    fun `mock has no model, and is not given one`() {

        // `mock` reports an empty `model_setting` because it has no model at
        // all. The fallback puts it on `llm.model`, which is harmless -
        // Gemini's name - and is the same path the picker used before.
        val state = state(
            primary = "mock",
            providers = listOf(provider("mock", modelSetting = "")),
        )

        assertEquals("llm.model", state.modelSetting)
        assertEquals("", state.activeModel)
    }

    // ------------------------------------------------------------------
    // What the row displays
    // ------------------------------------------------------------------

    @Test
    fun `the displayed model is the primary provider's, not Gemini's`() {

        // The row used to read `llm.model` directly, so with Claude primary
        // it displayed a Gemini model name that Claude would never be sent.
        val state = state(
            primary = "anthropic",
            llm = LlmConfigDto(
                provider = "anthropic",
                model = "gemini-3.6-flash",
                anthropicModel = "claude-sonnet-5",
            ),
            providers = listOf(
                provider(
                    "anthropic",
                    modelSetting = "llm.anthropic_model",
                    model = "claude-sonnet-5",
                )
            ),
        )

        assertEquals("claude-sonnet-5", state.activeModel)
    }

    @Test
    fun `a provider reporting no model falls back to the settings document`() {

        val state = state(
            primary = "gemini",
            llm = LlmConfigDto(provider = "gemini", model = "gemini-2.5-pro"),
            providers = listOf(provider("gemini", modelSetting = "llm.model")),
        )

        assertEquals("gemini-2.5-pro", state.activeModel)
    }

    @Test
    fun `the model choices are the primary provider's own`() {

        val state = state(
            primary = "anthropic",
            providers = listOf(
                provider("gemini", models = listOf("gemini-3.6-flash")),
                provider(
                    "anthropic",
                    models = listOf("claude-sonnet-5", "claude-opus-5"),
                ),
            ),
        )

        assertEquals(listOf("claude-sonnet-5", "claude-opus-5"), state.modelChoices)
    }

    @Test
    fun `an unknown provider offers no invented model list`() {

        // Free-text entry is what the Models screen falls back to. A guessed
        // list would go stale the week after it shipped.
        assertEquals(emptyList<String>(), state(primary = "openai").modelChoices)
        assertNull(state(primary = "openai").primaryProvider)
    }

    @Test
    fun `the primary is matched on the configured name, not on the flag`() {

        // `is_primary` comes from the same document, but a stale providers
        // response must never disagree with the settings document the rest of
        // the screen renders from.
        val state = state(
            primary = "groq",
            providers = listOf(
                ProviderDto(name = "gemini", label = "Gemini", isPrimary = true),
                ProviderDto(name = "groq", label = "Groq", isPrimary = false),
            ),
        )

        assertEquals("groq", state.primaryProvider?.name)
    }

    // ------------------------------------------------------------------

    private fun state(
        primary: String,
        providers: List<ProviderDto> = emptyList(),
        llm: LlmConfigDto? = null,
    ) = HubUiState(
        device = AuraSettings(serverUrl = "https://aura.example/", authToken = "t"),
        server = ServerState(
            loaded = true,
            reach = ServerReach.SettingsAvailable,
            config = EffectiveConfigDto(llm = llm ?: LlmConfigDto(provider = primary)),
            providers = providers,
        ),
    )

    private fun provider(
        name: String,
        modelSetting: String = "",
        model: String = "",
        models: List<String> = emptyList(),
    ) = ProviderDto(
        name = name,
        label = name,
        chat = true,
        configured = true,
        models = models,
        model = model,
        modelSetting = modelSetting,
    )
}
