package com.aura.companion.ui.hub

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.aura.companion.data.AuraError
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.AuraResult
import com.aura.companion.data.remote.EffectiveConfigDto
import com.aura.companion.data.remote.ProviderDto
import com.aura.companion.data.remote.ProviderHealthDto
import com.aura.companion.data.settings.AuraSettings
import com.aura.companion.data.settings.DeviceSettings
import com.aura.companion.data.settings.ThemeMode
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive

/**
 * The Control Hub's state, for every section.
 *
 * ONE VIEWMODEL, NOT ELEVEN
 * -------------------------
 * Every section renders from the same two server documents - `GET
 * /api/settings` and `GET /api/providers` - and a per-section ViewModel
 * would mean each screen re-fetching both on entry. Eleven ViewModels
 * would also mean eleven copies of "what does a 422 look like". The
 * sections are stateless Composables reading this.
 *
 * WHAT IS TRUE HERE
 * -----------------
 * [ServerState.config] is what the *server* is running on. It is replaced
 * from the server's own response after every PATCH rather than being
 * edited locally, so a value shown as on is on where it matters. That is
 * also what makes rule 13 hold - Android is a control surface, not the
 * source of truth.
 *
 * The device settings ([HubUiState.device]) are the exception, and only
 * for things that are genuinely device-local: the theme, dynamic colour,
 * the accessibility toggles that gate what this phone sends.
 *
 * NO POLLING
 * ----------
 * Loaded on entry and on pull-to-refresh. `providers/health` is cheap
 * enough to poll but polling it would keep the radio awake for a screen
 * nobody is looking at, so it refreshes with everything else.
 */
/**
 * How far the app got with the server, as a ladder.
 *
 * WHY A LADDER AND NOT A BOOLEAN
 * ------------------------------
 * The hub used to derive "connected" from whether `GET /api/settings`
 * returned a document, which conflated two unrelated facts. A server
 * running a build from before the Control Hub existed answers
 * `/api/health` and `/api/chat` normally and 404s `/api/settings` - and
 * the app called that "Disconnected / unexpected response (404)" while
 * chat was working in the next tab. That reading sends the user to
 * re-enter a token that was never wrong.
 *
 * Each rung is a separate, independently observable fact:
 *
 *   Unreachable        nothing answered - no route, timeout, refused
 *   Connected          something answered, but not as Aura-with-our-token
 *   Authenticated      `GET /api/health` returned 200, so the server is
 *                      Aura *and* the bearer token is accepted. Chat
 *                      works from here up.
 *   SettingsAvailable  `GET /api/settings` returned 200: the hub can
 *                      render and PATCH
 *   ProviderHealthy    the provider chain reports ready and is serving
 *                      from the requested provider
 *
 * `/api/health` is itself behind `verify_token` (`server/routes/health.py`),
 * which is why a 200 there proves both reachability and authentication and
 * why a 401 there is the only thing that stops at [Connected].
 */
enum class ServerReach {
    Unknown,
    Unreachable,
    Connected,
    Authenticated,
    SettingsAvailable,
    ProviderHealthy;

    fun atLeast(other: ServerReach): Boolean = ordinal >= other.ordinal
}

data class HubUiState(
    val device: AuraSettings = AuraSettings(),
    val server: ServerState = ServerState(),
    val loading: Boolean = false,
    val error: String? = null,
    /** Paths currently in flight, so their rows can show a spinner. */
    val pending: Set<String> = emptySet(),
    /** The last thing that happened, for a notice at the top of a section. */
    val notice: Notice? = null,
    val providerAction: ProviderAction = ProviderAction(),
) {
    /**
     * Whether Aura answered this app.
     *
     * Deliberately the health rung, not the settings rung: the question
     * the status line answers is "is my Aura up", and it is, even when
     * this build of the app knows about endpoints that deployment does
     * not. [settingsAccess] carries the narrower failure.
     */
    val connected: Boolean get() = server.reach.atLeast(ServerReach.Authenticated)

    /** True when the hub has a settings document to render and PATCH. */
    val settingsAvailable: Boolean get() = server.loaded

    /**
     * Why the settings document is or is not usable.
     *
     * The one place the answer is decided. Every consumer reads this rather
     * than re-deriving a sentence from [ServerState.loaded], which is what
     * used to make a rate limit, a cold start and a 500 all report as "this
     * server does not expose settings".
     */
    val settingsAccess: SettingsAccess
        get() = settingsAccess(
            loaded = server.loaded,
            connected = connected,
            error = server.settingsError,
        )

    /**
     * Whether the server accepts this setting.
     *
     * A phone newer than its server must not offer a control that will
     * come back as a 422. Before the first successful load `configurable`
     * is empty and everything is treated as supported - showing a whole
     * screen as unsupported because the network is slow would be worse
     * than a rejection the user can read.
     */
    fun supports(path: String): Boolean =
        server.configurable.isEmpty() || path in server.configurable

    fun lockedReason(path: String): String? = when {
        // Ordered so the *closest* reason wins. A reachable server whose
        // settings failed is a different instruction from an unreachable one,
        // and "check your connection" would be actively misleading. Which
        // failure it was comes from [settingsAccess], not from this `when`:
        // exactly one of its members claims the feature is absent.
        !server.loaded -> settingsAccess.reason.ifBlank { null }
        !supports(path) -> "This Aura server does not support this setting"
        else -> null
    }

    // ------------------------------------------------------------------
    // The primary provider, and where its model lives
    // ------------------------------------------------------------------

    /**
     * The provider Aura would use first.
     *
     * Matched on the configured name rather than trusting `is_primary`
     * alone, so a stale providers document cannot disagree with the
     * settings document the rest of the screen renders. Null when the
     * providers route has not answered, or names a provider this build
     * does not list.
     */
    val primaryProvider: ProviderDto?
        get() = server.providers.firstOrNull { it.name == server.config.llm.provider }

    /**
     * The settings path that holds the primary provider's model.
     *
     * WHY THIS IS NOT ALWAYS `llm.model`
     * ----------------------------------
     * It was, and that was a bug: the model picker wrote `llm.model` for
     * every provider, but `brain/router.py` reads `llm.anthropic_model` for
     * Anthropic, `llm.qwen_model` for Qwen, `llm.fallback_model` for
     * OpenRouter, and so on. Choosing a Claude model while Claude was
     * primary saved a name only Gemini would ever read - a control that
     * appeared to work and could not. The server reports the mapping per
     * provider ([ProviderDto.modelSetting]) so this stays in one place.
     *
     * `llm.model` remains the fallback: an older server sends no
     * `model_setting`, and on those the old behaviour is the correct one.
     */
    val modelSetting: String
        get() = primaryProvider?.modelSettingOr() ?: "llm.model"

    /**
     * The model the primary provider would actually be built with.
     *
     * Read from the providers document, which resolved it through the same
     * setting the router reads. Falls back to `llm.model` for a server that
     * does not report it.
     */
    val activeModel: String
        get() = primaryProvider?.model?.ifBlank { null } ?: server.config.llm.model

    /** Model names this build knows for the primary provider; may be empty. */
    val modelChoices: List<String>
        get() = primaryProvider?.models ?: emptyList()
}

data class ServerState(
    val loaded: Boolean = false,
    val reach: ServerReach = ServerReach.Unknown,
    /**
     * What went wrong with `GET /api/settings`, when the server is otherwise
     * up. Null when it loaded, or when the server itself is unreachable - in
     * that case the top-level error already says so and repeating it next to
     * every row is noise.
     *
     * The **typed** error, not a sentence: the wording is derived from it once,
     * by [settingsAccess]. Holding only a rendered string is what let five
     * screens each invent their own claim about why settings were missing, and
     * all five settled on the same wrong one.
     */
    val settingsError: AuraError? = null,
    /**
     * What went wrong with `GET /api/providers` or `/api/providers/health`.
     *
     * These used to fail silently - the provider section simply had nothing in
     * it, with no statement of why. A section that is empty for an unstated
     * reason reads as a broken app.
     */
    val providersError: AuraError? = null,
    val config: EffectiveConfigDto = EffectiveConfigDto(),
    val configurable: Set<String> = emptySet(),
    val providers: List<ProviderDto> = emptyList(),
    val health: ProviderHealthDto = ProviderHealthDto(),
    /** False when the server has no secret: saved keys die at restart. */
    val keysPersistent: Boolean = true,
    val keyStorageNote: String = "",
    /** Set after a PATCH the server said needs a restart. */
    val restartRequired: Boolean = false,
    /** From `GET /api/health`, for the diagnostics section. */
    val version: String = "",
    /** How long the server process has been up, per `/api/health`. */
    val uptimeSeconds: Double = 0.0,
    /**
     * The server's own subsystem report from `/api/health` - which parts of
     * Aura this deployment actually built. Rendered verbatim rather than
     * interpreted: the keys are the server's, and a build with a subsystem
     * this app has never heard of should still show it.
     */
    val runtime: Map<String, String> = emptyMap(),
)

/** Transient per-provider UI state: which one is being tested or saved. */
data class ProviderAction(
    val testing: String = "",
    val savingKey: String = "",
    val results: Map<String, TestOutcome> = emptyMap(),
    val keyError: String? = null,
)

data class TestOutcome(val ok: Boolean, val message: String)

data class Notice(val text: String, val kind: Kind) {
    enum class Kind { Info, Warning, Error }
}

class HubViewModel(
    private val settings: DeviceSettings,
    private val repository: AuraRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(HubUiState(device = settings.current))
    val state: StateFlow<HubUiState> = _state.asStateFlow()

    init {
        viewModelScope.launch {
            settings.settings.collect { device ->
                _state.update { it.copy(device = device) }
            }
        }
        refresh()
    }

    // ------------------------------------------------------------------
    // Loading
    // ------------------------------------------------------------------

    /**
     * Pull the server's configuration and provider state.
     *
     * THREE INDEPENDENT QUESTIONS, IN ORDER
     * -------------------------------------
     * 1. Is Aura there and does it accept my token? (`GET /api/health`)
     * 2. Can it tell me its settings? (`GET /api/settings`)
     * 3. Is its provider chain serving what I asked for?
     *
     * Each answer only ever moves its own rung of [ServerReach]. Step 1 is
     * what the status line reports, so a server that fails step 2 - an
     * older deployment without the hub API - shows as connected with the
     * settings section explaining itself, instead of the whole app
     * claiming to be offline. Step 1 is also the cheapest, so an
     * unreachable server costs one request rather than three.
     */
    fun refresh() {

        if (!settings.current.isConfigured) {
            _state.update {
                it.copy(loading = false, server = ServerState(), error = null)
            }
            return
        }

        _state.update { it.copy(loading = true, error = null) }

        viewModelScope.launch {

            // 1. Reachability and authentication, from the one route that
            //    every Aura build has ever had.
            val reachable = when (val health = repository.health()) {

                is AuraResult.Ok -> {
                    _state.update {
                        it.copy(
                            error = null,
                            server = it.server.copy(
                                reach = ServerReach.Authenticated,
                                version = health.value.version,
                                uptimeSeconds = health.value.uptimeSeconds,
                                runtime = health.value.runtime,
                            ),
                        )
                    }
                    true
                }

                is AuraResult.Failed -> {
                    _state.update {
                        it.copy(
                            loading = false,
                            error = health.error.userMessage,
                            server = it.server.copy(
                                loaded = false,
                                // A refused token or a 404 from /api/health
                                // means something answered; a timeout or a
                                // dead host means nothing did.
                                reach = when (health.error) {
                                    is AuraError.Offline,
                                    is AuraError.Timeout,
                                    is AuraError.NotConfigured,
                                    -> ServerReach.Unreachable

                                    else -> ServerReach.Connected
                                },
                                settingsError = null,
                            ),
                        )
                    }
                    false
                }
            }

            if (!reachable) return@launch

            // 2. The settings document. Its absence is a missing feature,
            //    not a missing server - and *which* absence it is has to
            //    survive to the UI, because "not on this server" and "the
            //    server is rate limiting me" are different instructions.
            when (val result = repository.loadSettings()) {

                is AuraResult.Ok -> _state.update {
                    it.copy(
                        loading = false,
                        error = null,
                        server = it.server.copy(
                            loaded = true,
                            reach = ServerReach.SettingsAvailable,
                            settingsError = null,
                            config = result.value.effective,
                            configurable = result.value.configurable.toSet(),
                            keysPersistent = result.value.providers.persistent,
                            keyStorageNote = result.value.providers.persistenceNote,
                        ),
                    )
                }

                is AuraResult.Failed -> _state.update {
                    it.copy(
                        loading = false,
                        // Not `error`: the server is up, and a banner
                        // saying otherwise contradicts the status line
                        // two lines above it.
                        error = null,
                        server = it.server.copy(
                            loaded = false,
                            settingsError = result.error,
                        ),
                    )
                }
            }

            refreshProviders()
        }
    }

    /**
     * Providers and chain health.
     *
     * Additive: a hub showing settings but not the chain is still useful,
     * so a failure here neither blanks the screen nor lowers [ServerReach]
     * below what the earlier steps established. It only ever raises the
     * top rung, and only when the chain is genuinely serving the requested
     * provider.
     *
     * A failure is recorded rather than dropped. It used to be dropped - both
     * calls were `if (result is AuraResult.Ok)` and nothing else - so a
     * provider route that 500ed left the section empty with no statement of
     * why, which reads as a broken app rather than an unanswered request.
     */
    fun refreshProviders() {
        viewModelScope.launch {

            repository.loadProviders().let { result ->
                when (result) {
                    is AuraResult.Ok -> _state.update {
                        it.copy(
                            server = it.server.copy(
                                providers = result.value.providers,
                                providersError = null,
                                keysPersistent = result.value.keyStorage.persistent,
                                keyStorageNote = result.value.keyStorage.persistenceNote,
                            )
                        )
                    }

                    is AuraResult.Failed -> _state.update {
                        it.copy(
                            server = it.server.copy(providersError = result.error)
                        )
                    }
                }
            }

            repository.providerHealth().let { result ->
                when (result) {
                    is AuraResult.Ok -> {

                        val health = result.value

                        _state.update {
                            it.copy(
                                server = it.server.copy(
                                    health = health,
                                    reach = if (health.ready && !health.inFallback) {
                                        ServerReach.ProviderHealthy
                                    } else {
                                        it.server.reach
                                    },
                                )
                            )
                        }
                    }

                    // Only when the list call had not already explained
                    // itself: one sentence about the provider routes is
                    // enough, and the first failure is the more specific one.
                    is AuraResult.Failed -> _state.update {
                        it.copy(
                            server = it.server.copy(
                                providersError = it.server.providersError
                                    ?: result.error
                            )
                        )
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Changing a server setting
    // ------------------------------------------------------------------

    fun setFlag(path: String, value: Boolean) = patch(path, JsonPrimitive(value))

    fun setText(path: String, value: String) = patch(path, JsonPrimitive(value))

    fun setNumber(path: String, value: Number) = patch(path, JsonPrimitive(value))

    fun setList(path: String, values: List<String>) =
        patch(path, JsonArray(values.map { JsonPrimitive(it) }))

    /**
     * Quiet hours, the one setting whose shape is a list of lists.
     *
     * It gets a typed helper rather than a raw JSON call site because the
     * server validates the structure - `[[22, 8]]`, hours in 0..23, at
     * most four windows - and a screen assembling that by hand is how the
     * shape drifts from what `_quiet_hours` accepts.
     */
    fun setQuietHours(windows: List<List<Int>>) = patch(
        "proactive.quiet_hours",
        JsonArray(
            windows.map { window ->
                JsonArray(window.map { JsonPrimitive(it) })
            }
        ),
    )

    /**
     * One setting, to the server, and back.
     *
     * The row shows a spinner rather than the new value while this is in
     * flight, and the server's own `effective` config replaces the local
     * copy on success. A toggle therefore cannot end up showing a state
     * the server does not agree with - which is the failure mode that
     * makes a settings screen untrustworthy.
     *
     * `restart_required` is surfaced, not swallowed: a change that was
     * saved but is not yet live has to say so.
     */
    private fun patch(path: String, value: JsonElement) {

        _state.update { it.copy(pending = it.pending + path, notice = null) }

        viewModelScope.launch {

            when (val result = repository.patchSettings(mapOf(path to value))) {

                is AuraResult.Ok -> {

                    val report = result.value

                    val needsRestart = report.restartRequired.isNotEmpty()

                    _state.update {
                        it.copy(
                            pending = it.pending - path,
                            server = it.server.copy(
                                config = report.effective,
                                restartRequired = it.server.restartRequired || needsRestart,
                            ),
                            notice = when {
                                needsRestart -> Notice(
                                    "Saved. Restart Aura for this to take effect.",
                                    Notice.Kind.Warning,
                                )
                                !report.persistent -> Notice(
                                    "Applied, but this will not survive a server restart.",
                                    Notice.Kind.Warning,
                                )
                                else -> null
                            },
                        )
                    }

                    // A provider or model change reshapes the chain, so the
                    // provider section must not keep showing the old one.
                    if (path.startsWith("llm.")) refreshProviders()
                }

                is AuraResult.Failed -> _state.update {
                    it.copy(
                        pending = it.pending - path,
                        notice = Notice(result.error.userMessage, Notice.Kind.Error),
                    )
                }
            }
        }
    }

    /** Drop server-side overrides for these paths and reload. */
    fun resetSettings(paths: List<String>? = null) {

        _state.update { it.copy(loading = true, notice = null) }

        viewModelScope.launch {

            val result = repository.resetSettings(paths)

            _state.update {
                it.copy(
                    loading = false,
                    notice = when (result) {
                        is AuraResult.Ok -> Notice(
                            result.value.message.ifBlank { "Settings reverted." },
                            if (result.value.needsRestart) {
                                Notice.Kind.Warning
                            } else {
                                Notice.Kind.Info
                            },
                        )
                        is AuraResult.Failed ->
                            Notice(result.error.userMessage, Notice.Kind.Error)
                    },
                )
            }

            if (result is AuraResult.Ok) refresh()
        }
    }

    // ------------------------------------------------------------------
    // Providers
    // ------------------------------------------------------------------

    fun setPrimaryProvider(name: String) = setText("llm.provider", name)

    /**
     * Set the primary provider's model.
     *
     * Writes whichever `llm.*_model` setting that provider reads, per
     * [HubUiState.modelSetting] - not a hardcoded `llm.model`, which only
     * Gemini reads. `setting` is a parameter so a caller with a specific
     * provider in hand (the fallback editor) can name its path directly.
     */
    fun setModel(name: String, setting: String = _state.value.modelSetting) =
        setText(setting, name)

    /**
     * Add or remove a provider from the fallback chain.
     *
     * Order is preserved on removal and appended on addition, because the
     * chain is tried in order and reshuffling it silently would change
     * which provider answers.
     */
    fun toggleFallback(name: String) {

        val current = _state.value.server.config.llm.fallbackProviders

        val next = if (name in current) current - name else current + name

        setList("llm.fallback_providers", next)
    }

    /** Send one real prompt to a provider. Costs a token; a button only. */
    fun testProvider(name: String) {

        _state.update {
            it.copy(providerAction = it.providerAction.copy(testing = name))
        }

        viewModelScope.launch {

            val outcome = when (val result = repository.testProvider(name)) {

                is AuraResult.Ok -> {

                    val body = result.value

                    if (body.ok) {
                        TestOutcome(
                            ok = true,
                            message = "Answered in ${body.latencyMs} ms" +
                                if (body.model.isNotBlank()) " (${body.model})" else "",
                        )
                    } else {
                        // `error` is a category and `detail` an exception
                        // class name at most - see the route's docstring.
                        TestOutcome(
                            ok = false,
                            message = body.error.ifBlank { "Did not respond" },
                        )
                    }
                }

                is AuraResult.Failed -> TestOutcome(false, result.error.userMessage)
            }

            _state.update {
                it.copy(
                    providerAction = it.providerAction.copy(
                        testing = "",
                        results = it.providerAction.results + (name to outcome),
                    )
                )
            }
        }
    }

    // ------------------------------------------------------------------
    // API keys
    // ------------------------------------------------------------------

    /**
     * Send a key to the server.
     *
     * `key` is a parameter and is never assigned to state, a field, or a
     * log. What comes back is a mask, and the provider list is reloaded
     * from the server so what the screen shows is what the server stored.
     */
    fun saveProviderKey(provider: String, key: String) {

        if (key.isBlank()) return

        _state.update {
            it.copy(
                providerAction = it.providerAction.copy(
                    savingKey = provider,
                    keyError = null,
                )
            )
        }

        viewModelScope.launch {

            when (val result = repository.setProviderKey(provider, key)) {

                is AuraResult.Ok -> {

                    val body = result.value

                    _state.update {
                        it.copy(
                            providerAction = it.providerAction.copy(
                                savingKey = "",
                                keyError = null,
                            ),
                            notice = if (!body.persistent) {
                                Notice(
                                    body.warning.ifBlank {
                                        "Key applied, but it will not survive a " +
                                            "server restart."
                                    },
                                    Notice.Kind.Warning,
                                )
                            } else {
                                Notice("Key saved.", Notice.Kind.Info)
                            },
                        )
                    }

                    refreshProviders()
                }

                is AuraResult.Failed -> _state.update {
                    it.copy(
                        providerAction = it.providerAction.copy(
                            savingKey = "",
                            keyError = result.error.userMessage,
                        )
                    )
                }
            }
        }
    }

    fun deleteProviderKey(provider: String) {

        _state.update {
            it.copy(providerAction = it.providerAction.copy(savingKey = provider))
        }

        viewModelScope.launch {

            val result = repository.deleteProviderKey(provider)

            _state.update {
                it.copy(
                    providerAction = it.providerAction.copy(savingKey = ""),
                    notice = when (result) {
                        is AuraResult.Ok -> Notice("Key removed.", Notice.Kind.Info)
                        is AuraResult.Failed ->
                            Notice(result.error.userMessage, Notice.Kind.Error)
                    },
                )
            }

            if (result is AuraResult.Ok) refreshProviders()
        }
    }

    // ------------------------------------------------------------------
    // Device settings
    //
    // These do not travel. They gate what this phone does, or how it
    // looks, and the server has no opinion about either.
    // ------------------------------------------------------------------

    fun setScreenObservation(enabled: Boolean) = settings.setScreenObservation(enabled)

    fun setNotifications(enabled: Boolean) = settings.setNotifications(enabled)

    fun setUploadScreenshots(enabled: Boolean) = settings.setUploadScreenshots(enabled)

    fun setThemeMode(mode: ThemeMode) = settings.setThemeMode(mode)

    fun setDynamicColour(enabled: Boolean) = settings.setDynamicColour(enabled)

    fun dismissNotice() = _state.update { it.copy(notice = null) }

    companion object {
        fun factory(
            settings: DeviceSettings,
            repository: AuraRepository,
        ): ViewModelProvider.Factory = object : ViewModelProvider.Factory {

            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T =
                HubViewModel(settings, repository) as T
        }
    }
}
