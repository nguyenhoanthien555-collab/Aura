package com.aura.companion.ui.components

import com.aura.companion.data.remote.ProviderDto
import com.aura.companion.data.remote.ProviderStateDto
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What a provider card is allowed to say.
 *
 * Two things are being pinned here, and only one of them is cosmetic.
 *
 * The first is that no line ever carries a secret. The card renders on a
 * phone, gets screenshotted into bug reports, and sits behind no auth once
 * it is on screen - so "a variable's name is not its value" has to be a
 * property of the code, not a habit. The endpoint line is the sharp edge:
 * some gateways carry a token in the base URL's query string, so an
 * overridden endpoint must never be printed, only acknowledged.
 *
 * The second is that the card does not invent certainty. `standby` means
 * nothing has been asked of that provider yet, and rendering it as "healthy"
 * would be a claim no request ever supported.
 */
class ProviderSummaryTest {

    // ------------------------------------------------------------------
    // Secrets
    // ------------------------------------------------------------------

    @Test
    fun `an overridden endpoint is acknowledged, never printed`() {

        // The override is the case that could leak: this is what a gateway
        // URL with a token in it looks like, and the server deliberately
        // never sends it. The app must not imply it has one either.
        val facts = providerFacts(
            provider(
                name = "openai",
                apiBase = "https://api.openai.com/v1",
                apiBaseOverridden = true,
                baseUrlEnv = "OPENAI_BASE_URL",
            )
        )

        assertTrue(facts.contains("Custom endpoint (via OPENAI_BASE_URL)"))

        // The default endpoint is not the effective one, so printing it
        // next to an override would be worse than saying nothing.
        assertTrue(
            "the default endpoint must not be shown once it is overridden",
            facts.none { it.contains("api.openai.com") },
        )
    }

    @Test
    fun `an override with no named variable still does not guess a URL`() {

        assertEquals(
            "Custom endpoint",
            endpointFact(provider("groq", apiBase = "https://api.groq.com", apiBaseOverridden = true)),
        )
    }

    @Test
    fun `no fact reproduces the key or its mask`() {

        // `keyMasked` is already safe - the server built it - but the card
        // has no reason to repeat it in a fact line, and a future edit that
        // started doing so should fail here.
        val facts = providerFacts(
            provider(
                name = "anthropic",
                configured = true,
                keyMasked = "sk-ant••••••••7X2",
                keySource = "store",
                apiKeyEnv = "ANTHROPIC_API_KEY",
                model = "claude-sonnet-5",
            ),
            health = ProviderStateDto(configured = true, healthy = true, state = "active"),
        )

        listOf("sk-ant", "••••", "7X2").forEach { fragment ->
            assertTrue(
                "no fact may contain \"$fragment\": $facts",
                facts.none { it.contains(fragment) },
            )
        }
    }

    @Test
    fun `a host-provided key names the variable and nothing else`() {

        assertEquals(
            "From GEMINI_API_KEY",
            keySourceFact(
                provider("gemini", configured = true, keySource = "environment", apiKeyEnv = "GEMINI_API_KEY")
            ),
        )
    }

    @Test
    fun `a keyless provider is not asked about keys`() {

        // Ollama and mock need nothing. A "key variable" line on them would
        // send someone hunting for a key that does not exist.
        assertNull(keySourceFact(provider("ollama", keyless = true, apiKeyEnv = "OLLAMA_API_KEY")))
    }

    @Test
    fun `an unconfigured provider is told where its key would come from`() {

        assertEquals(
            "Key variable: XAI_API_KEY",
            keySourceFact(provider("xai", configured = false, apiKeyEnv = "XAI_API_KEY")),
        )
    }

    @Test
    fun `a key already in the store needs no explaining`() {

        // It is configured, from the store, and the row above already shows
        // the mask. A second line would be noise.
        assertNull(
            keySourceFact(
                provider("openai", configured = true, keySource = "store", apiKeyEnv = "OPENAI_API_KEY")
            )
        )
    }

    // ------------------------------------------------------------------
    // Claims about health
    // ------------------------------------------------------------------

    @Test
    fun `standby is not reported as healthy`() {

        // Nothing has been sent to it. "Healthy" would be an observation
        // that was never made.
        val fact = healthFact(ProviderStateDto(configured = true, healthy = true, state = "standby"))

        assertEquals("Standby - in the chain, not yet needed", fact)
    }

    @Test
    fun `a provider the health route never mentioned says nothing`() {

        // Not reported is not unwell. `/api/providers/health` can 404 on an
        // older server while everything else works.
        assertNull(healthFact(null))
        assertNull(healthFact(ProviderStateDto(state = "")))
    }

    @Test
    fun `an error carries the reason when there is one`() {

        assertEquals("State unavailable", healthFact(ProviderStateDto(state = "error")))

        assertEquals(
            "State unavailable (timed out)",
            healthFact(ProviderStateDto(state = "error", problem = "timed out")),
        )
    }

    @Test
    fun `a state this build does not know is shown, not swallowed`() {

        // A server newer than the app. Its word is better than silence.
        assertEquals("throttled", healthFact(ProviderStateDto(state = "throttled")))
    }

    @Test
    fun `unconfigured adds no line, because the key line already said it`() {

        assertNull(healthFact(ProviderStateDto(state = "unconfigured")))
    }

    // ------------------------------------------------------------------
    // The model line
    // ------------------------------------------------------------------

    @Test
    fun `the model shown is the one the router would use`() {

        assertEquals(
            "Model: claude-sonnet-5",
            modelFact(provider("anthropic", model = "claude-sonnet-5")),
        )
    }

    @Test
    fun `a provider with choices but no pick says so rather than guessing one`() {

        // Picking `models.first()` here would show a model the server never
        // said it would use - the same class of lie the model-setting bug
        // was. See `ModelSettingTest`.
        assertEquals(
            "Model: provider default",
            modelFact(provider("groq", models = listOf("llama-3.3-70b", "llama-3.1-8b"))),
        )
    }

    @Test
    fun `mock has no model line at all`() {

        assertNull(modelFact(provider("mock", keyless = true)))
    }

    // ------------------------------------------------------------------
    // The card as a whole
    // ------------------------------------------------------------------

    @Test
    fun `facts read in order, and a quiet server makes a short card`() {

        val facts = providerFacts(
            provider(
                name = "deepseek",
                configured = true,
                model = "deepseek-chat",
                apiBase = "https://api.deepseek.com",
                keySource = "environment",
                apiKeyEnv = "DEEPSEEK_API_KEY",
            ),
            health = ProviderStateDto(configured = true, state = "idle"),
        )

        assertEquals(
            listOf(
                "Model: deepseek-chat",
                "https://api.deepseek.com",
                "From DEEPSEEK_API_KEY",
                "Configured, not in the chain",
            ),
            facts,
        )

        // A server that reported almost nothing gets a card with almost
        // nothing on it, rather than four rows of "unknown".
        assertEquals(emptyList<String>(), providerFacts(provider("something-new")))
    }

    // ------------------------------------------------------------------

    private fun provider(
        name: String,
        keyless: Boolean = false,
        configured: Boolean = false,
        model: String = "",
        models: List<String> = emptyList(),
        keyMasked: String = "",
        keySource: String = "",
        apiBase: String = "",
        apiBaseOverridden: Boolean = false,
        baseUrlEnv: String = "",
        apiKeyEnv: String = "",
    ) = ProviderDto(
        name = name,
        label = name,
        chat = true,
        keyless = keyless,
        models = models,
        configured = configured,
        keyMasked = keyMasked,
        keySource = keySource,
        model = model,
        apiBase = apiBase,
        apiBaseOverridden = apiBaseOverridden,
        baseUrlEnv = baseUrlEnv,
        apiKeyEnv = apiKeyEnv,
    )
}
