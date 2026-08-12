package com.aura.companion.ui.components

import com.aura.companion.data.remote.ProviderDto
import com.aura.companion.data.remote.ProviderStateDto

/**
 * The facts one provider card states, as plain strings.
 *
 * WHY THIS IS NOT INSIDE THE COMPOSABLE
 * -------------------------------------
 * This file imports no Compose and no Android type, so it runs under
 * `:app:testDebugUnitTest`. The Compose UI test harness in this module is
 * `androidTestImplementation` only - there is no Robolectric and no JVM
 * Compose runtime - so anything that must be *asserted* rather than
 * eyeballed has to live in a function like this one. `ProviderCard` renders
 * the list; the decisions about what it says are here, under test.
 *
 * WHAT IT MAY NOT SAY
 * -------------------
 * No line here can contain a secret. [ProviderDto.keyMasked] is a mask the
 * server computed and there is no field in that class that could hold a real
 * key. [ProviderDto.apiBase] is the *default* endpoint, never the effective
 * one: an override can carry a token in its query string on some gateways,
 * so the server reports a boolean instead and this file says only that an
 * override is in effect and which variable sets it. A variable's name is
 * not its value.
 */

/**
 * Where this provider's model comes from and where it lives.
 *
 * Returns null when the server does not report a model for it - `mock` has
 * none, and a server older than the `model` field sends nothing.
 */
fun modelFact(provider: ProviderDto): String? = when {
    provider.model.isNotBlank() -> "Model: ${provider.model}"
    provider.models.isNotEmpty() -> "Model: provider default"
    else -> null
}

/**
 * The endpoint, in the only terms that are safe to render.
 *
 * An override wins the line, because it is the fact that explains a
 * provider behaving unlike its vendor - and naming the variable is what
 * makes it fixable by whoever deployed Aura.
 */
fun endpointFact(provider: ProviderDto): String? = when {
    provider.apiBaseOverridden ->
        if (provider.baseUrlEnv.isBlank()) {
            "Custom endpoint"
        } else {
            "Custom endpoint (via ${provider.baseUrlEnv})"
        }
    provider.apiBase.isNotBlank() -> provider.apiBase
    else -> null
}

/** Which environment variable a host-provided key would come from. */
fun keySourceFact(provider: ProviderDto): String? = when {
    provider.keyless || provider.apiKeyEnv.isBlank() -> null
    provider.keySource == "environment" -> "From ${provider.apiKeyEnv}"
    provider.configured -> null
    else -> "Key variable: ${provider.apiKeyEnv}"
}

/**
 * The provider's health, from `GET /api/providers/health`.
 *
 * The server reports these without spending a token per provider, so the
 * words here are careful about what was actually observed: `standby` has
 * never been asked anything, and saying "healthy" of it would be a claim
 * nobody made. A null state means the route did not report this provider,
 * which is not the same as the provider being unwell.
 */
fun healthFact(health: ProviderStateDto?): String? = when (health?.state) {
    null, "" -> null
    "active" -> "Serving now"
    "standby" -> "Standby - in the chain, not yet needed"
    "failed" -> "Tried and did not answer"
    "idle" -> "Configured, not in the chain"
    "unconfigured" -> null // The key line already says this.
    "error" ->
        if (health.problem.isBlank()) {
            "State unavailable"
        } else {
            "State unavailable (${health.problem})"
        }
    // A build newer than this app: show its word rather than nothing.
    else -> health.state
}

/**
 * Everything the card states beneath the provider's name, in order.
 *
 * Order is deliberate: what it will run, where it will run it, then how it
 * is doing. Nulls are dropped, so a provider the server said little about
 * gets a short card rather than a column of "unknown".
 */
fun providerFacts(
    provider: ProviderDto,
    health: ProviderStateDto? = null,
): List<String> = listOfNotNull(
    modelFact(provider),
    endpointFact(provider),
    keySourceFact(provider),
    healthFact(health),
)
