package com.aura.companion.ui.hub

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.AuraResult
import com.aura.companion.data.remote.EffectiveConfigDto
import com.aura.companion.data.remote.ProviderDto
import com.aura.companion.data.remote.ProviderHealthDto
import com.aura.companion.data.settings.AuraSettings
import com.aura.companion.data.settings.SettingsStore
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
    val connected: Boolean get() = server.loaded

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
        !server.loaded -> "Connect to Aura to change this"
        !supports(path) -> "This Aura server does not support this setting"
        else -> null
    }
}

data class ServerState(
    val loaded: Boolean = false,
    val config: EffectiveConfigDto = EffectiveConfigDto(),
    val configurable: Set<String> = emptySet(),
    val providers: List<ProviderDto> = emptyList(),
    val health: ProviderHealthDto = ProviderHealthDto(),
    /** False when the server has no secret: saved keys die at restart. */
    val keysPersistent: Boolean = true,
    val keyStorageNote: String = "",
    /** Set after a PATCH the server said needs a restart. */
    val restartRequired: Boolean = false,
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
    private val settings: SettingsStore,
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
     * Settings first: without them the hub has nothing to render, so a
     * failure there is the one worth reporting. Providers and health are
     * additive - a hub that shows the settings but not the chain is still
     * useful, so their failures do not blank the screen.
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

            when (val result = repository.loadSettings()) {

                is AuraResult.Ok -> _state.update {
                    it.copy(
                        loading = false,
                        error = null,
                        server = it.server.copy(
                            loaded = true,
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
                        error = result.error.userMessage,
                        server = it.server.copy(loaded = false),
                    )
                }
            }

            refreshProviders()
        }
    }

    /** Providers and chain health. Silent on failure; the hub still renders. */
    fun refreshProviders() {
        viewModelScope.launch {

            repository.loadProviders().let { result ->
                if (result is AuraResult.Ok) {
                    _state.update {
                        it.copy(
                            server = it.server.copy(
                                providers = result.value.providers,
                                keysPersistent = result.value.keyStorage.persistent,
                                keyStorageNote = result.value.keyStorage.persistenceNote,
                            )
                        )
                    }
                }
            }

            repository.providerHealth().let { result ->
                if (result is AuraResult.Ok) {
                    _state.update {
                        it.copy(server = it.server.copy(health = result.value))
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

    fun setModel(name: String) = setText("llm.model", name)

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
            settings: SettingsStore,
            repository: AuraRepository,
        ): ViewModelProvider.Factory = object : ViewModelProvider.Factory {

            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T =
                HubViewModel(settings, repository) as T
        }
    }
}
