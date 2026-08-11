package com.aura.companion.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

/**
 * Wire types for the Control Hub.
 *
 * These mirror `server/routes/settings.py`. They live in their own file
 * rather than in [Dto] because they are a different conversation: [Dto]
 * carries messages, this carries configuration.
 *
 * WHY EVERY FIELD HAS A DEFAULT
 * -----------------------------
 * The server merges `DEFAULT_CONFIG`, `config.yaml` and the Control Hub
 * overlay, and some keys exist in none of them until somebody sets one -
 * `llm.timeout` is read by `brain/router.py` but is not in
 * `DEFAULT_CONFIG`, so a stock server never sends it. A non-null field
 * without a default would make the whole settings screen fail to parse
 * because one optional number was absent. Defaults here mean a missing
 * key renders as "not set" instead of as a crash.
 *
 * `ignoreUnknownKeys` is on in [ApiFactory], so the rest of the config
 * tree - avatar, plugins, logging, personality - arrives and is dropped.
 * That is deliberate: this app renders the settings it can actually
 * change, and the allow-list on the server is the shorter list.
 *
 * NO KEY EVER APPEARS HERE
 * ------------------------
 * [ProviderDto.keyMasked] is a mask (`••••••••ABCD`) the server computed;
 * there is no field anywhere in this file that could hold a real API key
 * arriving from the server, because no endpoint returns one.
 * [ApiKeyRequestDto] is write-only and travels in one direction.
 */

// ----------------------------------------------------------------------
// GET /api/settings
// ----------------------------------------------------------------------

@Serializable
data class SettingsResponseDto(
    val effective: EffectiveConfigDto = EffectiveConfigDto(),
    val providers: KeyStateDto = KeyStateDto(),
    /**
     * The dotted paths this server build accepts.
     *
     * Rendered rather than assumed: a phone that is newer than the server
     * must not offer a control the server will reject with a 422. A path
     * missing from here is shown as unsupported instead.
     */
    val configurable: List<String> = emptyList(),
)

@Serializable
data class KeyStateDto(
    /** Whether stored keys survive a restart. False means no server secret. */
    val persistent: Boolean = false,
    @SerialName("persistence_note") val persistenceNote: String = "",
)

@Serializable
data class EffectiveConfigDto(
    val app: AppConfigDto = AppConfigDto(),
    val llm: LlmConfigDto = LlmConfigDto(),
    val memory: MemoryConfigDto = MemoryConfigDto(),
    val proactive: ProactiveConfigDto = ProactiveConfigDto(),
    val vision: VisionConfigDto = VisionConfigDto(),
    val voice: VoiceConfigDto = VoiceConfigDto(),
    val tools: ToolsConfigDto = ToolsConfigDto(),
    val server: ServerConfigDto = ServerConfigDto(),
)

@Serializable
data class AppConfigDto(
    val name: String = "Aura",
    val version: String = "",
)

@Serializable
data class LlmConfigDto(
    val provider: String = "",
    val model: String = "",
    @SerialName("fallback_providers") val fallbackProviders: List<String> = emptyList(),
    @SerialName("fallback_model") val fallbackModel: String = "",
    @SerialName("groq_model") val groqModel: String = "",
    @SerialName("mistral_model") val mistralModel: String = "",
    @SerialName("ollama_model") val ollamaModel: String = "",
    val temperature: Double = 0.7,
    @SerialName("max_output_tokens") val maxOutputTokens: Int = 768,
    /** Absent from `DEFAULT_CONFIG`; the router's own fallback is 45s. */
    val timeout: Double = 45.0,
)

@Serializable
data class MemoryConfigDto(
    val recall: Boolean = false,
    val profile: Boolean = true,
    val pipeline: Boolean = true,
    @SerialName("history_limit") val historyLimit: Int = 20,
    @SerialName("retrieval_scope") val retrievalScope: Int = 500,
)

@Serializable
data class ProactiveConfigDto(
    val enabled: Boolean = false,
    @SerialName("cooldown_seconds") val cooldownSeconds: Double = 7200.0,
    @SerialName("max_per_day") val maxPerDay: Int = 4,
    /** `[[22, 8]]` - windows that may wrap midnight. */
    @SerialName("quiet_hours") val quietHours: List<List<Int>> = emptyList(),
    @SerialName("duplicate_window_seconds") val duplicateWindowSeconds: Double = 21600.0,
    @SerialName("similarity_threshold") val similarityThreshold: Double = 0.6,
)

@Serializable
data class VisionConfigDto(
    val enabled: Boolean = false,
    @SerialName("cloud_model") val cloudModel: String = "",
    @SerialName("ollama_model") val ollamaModel: String = "",
)

@Serializable
data class VoiceConfigDto(
    val tts: VoiceChannelDto = VoiceChannelDto(),
    val stt: VoiceChannelDto = VoiceChannelDto(),
)

@Serializable
data class VoiceChannelDto(
    val enabled: Boolean = false,
    val provider: String = "",
)

@Serializable
data class ToolsConfigDto(
    val enabled: Boolean = false,
    val allowed: List<String> = emptyList(),
)

@Serializable
data class ServerConfigDto(
    val screen: ScreenConfigDto = ScreenConfigDto(),
    val companion: CompanionConfigDto = CompanionConfigDto(),
)

@Serializable
data class ScreenConfigDto(
    val enabled: Boolean = false,
    @SerialName("min_interval") val minInterval: Double = 8.0,
)

@Serializable
data class CompanionConfigDto(
    val enabled: Boolean = false,
)

// ----------------------------------------------------------------------
// PATCH /api/settings
// ----------------------------------------------------------------------

/**
 * A partial settings update.
 *
 * `settings` is a [JsonObject] rather than a typed class because the
 * server's contract is "send only what changed": a typed body with
 * nullable fields would serialise every unset field as `null` (the shared
 * `Json` has `encodeDefaults = true`), and the server's validators reject
 * `null` for a provider name or a temperature. One toggle would fail on
 * behalf of thirty others.
 *
 * The dynamic shape is confined to this one field. Callers reach it
 * through the typed helpers on `AuraRepository`, never by building JSON
 * at a call site.
 */
@Serializable
data class SettingsPatchDto(
    val settings: JsonObject,
)

@Serializable
data class SettingsPatchResponseDto(
    /** Paths that are in effect now. */
    val applied: List<String> = emptyList(),
    /** Paths saved on the server but needing a restart to take effect. */
    @SerialName("restart_required") val restartRequired: List<String> = emptyList(),
    val persistent: Boolean = true,
    @SerialName("needs_restart") val needsRestart: Boolean = false,
    val effective: EffectiveConfigDto = EffectiveConfigDto(),
)

@Serializable
data class SettingsResetRequestDto(
    /** Empty means "everything". */
    val paths: List<String>? = null,
)

@Serializable
data class SettingsResetResponseDto(
    val reset: List<String> = emptyList(),
    @SerialName("needs_restart") val needsRestart: Boolean = false,
    val message: String = "",
)

// ----------------------------------------------------------------------
// Providers
// ----------------------------------------------------------------------

@Serializable
data class ProvidersResponseDto(
    val providers: List<ProviderDto> = emptyList(),
    val primary: String = "",
    @SerialName("fallback_providers") val fallbackProviders: List<String> = emptyList(),
    /** What was actually built, e.g. `gemini->groq`. */
    @SerialName("active_chain") val activeChain: String = "",
    @SerialName("key_storage") val keyStorage: KeyStateDto = KeyStateDto(),
)

/**
 * One provider, as this server build implements it.
 *
 * The capability flags are read off the provider classes, not off the
 * vendors' documentation - Groq's API streams, but `GroqProvider` has no
 * `stream()`, so [streaming] is false. The UI renders these rather than
 * assuming, which is the difference between a badge that is true and a
 * badge that is marketing.
 */
@Serializable
data class ProviderDto(
    val name: String,
    val label: String = "",
    val chat: Boolean = false,
    val streaming: Boolean = false,
    val tools: Boolean = false,
    val vision: Boolean = false,
    /** True for providers that need no API key at all (ollama, mock). */
    val keyless: Boolean = false,
    val models: List<String> = emptyList(),
    val configured: Boolean = false,
    /** `••••••••ABCD`, or empty when nothing is stored. */
    @SerialName("key_masked") val keyMasked: String = "",
    /** `"store"`, `"environment"`, or empty. */
    @SerialName("key_source") val keySource: String = "",
    @SerialName("is_primary") val isPrimary: Boolean = false,
    @SerialName("is_fallback") val isFallback: Boolean = false,
)

@Serializable
data class ProviderHealthDto(
    val requested: String = "",
    val active: String = "",
    val chain: List<String> = emptyList(),
    /** True when Aura is answering from a substitute, not the chosen provider. */
    @SerialName("in_fallback") val inFallback: Boolean = false,
    val problems: List<String> = emptyList(),
    val ready: Boolean = false,
)

@Serializable
data class ProviderTestRequestDto(
    val provider: String,
    val model: String? = null,
)

@Serializable
data class ProviderTestResponseDto(
    val provider: String = "",
    val ok: Boolean = false,
    val model: String = "",
    @SerialName("latency_ms") val latencyMs: Int = 0,
    /** A category - "not configured", "unreachable" - never a raw error. */
    val error: String = "",
    /** An exception *class name* at most. Never a message, never a key. */
    val detail: String = "",
)

// ----------------------------------------------------------------------
// API keys
// ----------------------------------------------------------------------

/** Write-only. Nothing in this app ever receives one of these back. */
@Serializable
data class ApiKeyRequestDto(
    val key: String,
)

@Serializable
data class ApiKeyResponseDto(
    val provider: String = "",
    val saved: Boolean = false,
    val deleted: Boolean = false,
    /** False when the server has no secret and the key dies at restart. */
    val persistent: Boolean = true,
    @SerialName("key_masked") val keyMasked: String = "",
    val warning: String = "",
)
