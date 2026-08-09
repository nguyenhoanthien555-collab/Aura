package com.aura.companion.ui.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.AuraResult
import com.aura.companion.data.settings.SettingsStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * Settings state.
 *
 * The URL and token live here as plain draft strings while the user is
 * typing; they only reach [SettingsStore] - and therefore the encrypted
 * store - when saved.
 */
data class SettingsUiState(
    val serverUrl: String = "",
    val authToken: String = "",
    val deviceId: String = "",
    val screenObservation: Boolean = false,
    val notifications: Boolean = true,
    val uploadScreenshots: Boolean = false,
    val isTesting: Boolean = false,
    val testResult: TestResult? = null,
    val savedAt: Long = 0,
) {
    /** True when the configured URL is plaintext HTTP to a non-local host. */
    val insecureRemote: Boolean
        get() {
            val url = serverUrl.trim()
            if (!url.startsWith("http://")) return false
            val host = url.removePrefix("http://").substringBefore("/").substringBefore(":")
            return !(host == "localhost" ||
                host == "10.0.2.2" ||
                host.startsWith("192.168.") ||
                host.startsWith("10.") ||
                host.startsWith("172.") ||
                host.endsWith(".local"))
        }
}

sealed interface TestResult {
    data class Success(val provider: String, val version: String) : TestResult
    data class Failure(val message: String) : TestResult
}

/**
 * Drives the settings screen.
 *
 * "Test connection" exists because the alternative is discovering a typo
 * in a server address by sending a message and getting an error that could
 * equally mean the server is down.
 */
class SettingsViewModel(
    private val settings: SettingsStore,
    private val repository: AuraRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(
        settings.current.let {
            SettingsUiState(
                serverUrl = it.serverUrl,
                authToken = it.authToken,
                deviceId = it.deviceId,
                screenObservation = it.screenObservationEnabled,
                notifications = it.notificationsEnabled,
                uploadScreenshots = it.uploadScreenshots,
            )
        }
    )
    val state: StateFlow<SettingsUiState> = _state.asStateFlow()

    fun onUrlChanged(value: String) {
        _state.update { it.copy(serverUrl = value, testResult = null) }
    }

    fun onTokenChanged(value: String) {
        _state.update { it.copy(authToken = value, testResult = null) }
    }

    /**
     * Screen observation is off unless the user turns it on, here, on
     * purpose. Nothing else in the app may set this.
     */
    fun onScreenObservationChanged(enabled: Boolean) {
        settings.setScreenObservation(enabled)
        _state.update { it.copy(screenObservation = enabled) }
    }

    fun onNotificationsChanged(enabled: Boolean) {
        settings.setNotifications(enabled)
        _state.update { it.copy(notifications = enabled) }
    }

    fun onUploadScreenshotsChanged(enabled: Boolean) {
        settings.setUploadScreenshots(enabled)
        _state.update { it.copy(uploadScreenshots = enabled) }
    }

    /**
     * Persist, then normalise the field to whatever was actually stored.
     *
     * The store adds a scheme and a trailing slash. Showing the raw text
     * back would leave the user believing they saved something different
     * from what the app will request.
     */
    fun save() {

        val current = _state.value

        settings.setConnection(current.serverUrl, current.authToken)

        // A new server, or a new token, is a new conversation.
        repository.resetSession()

        _state.update {
            it.copy(
                serverUrl = settings.current.serverUrl,
                authToken = settings.current.authToken,
                savedAt = System.currentTimeMillis(),
                testResult = null,
            )
        }
    }

    fun testConnection() {

        save()

        _state.update { it.copy(isTesting = true, testResult = null) }

        viewModelScope.launch {

            val result = when (val health = repository.health()) {

                is AuraResult.Ok -> TestResult.Success(
                    provider = health.value.runtime["llm_provider"] ?: "unknown",
                    version = health.value.version,
                )

                is AuraResult.Failed -> TestResult.Failure(
                    health.error.userMessage
                )
            }

            _state.update { it.copy(isTesting = false, testResult = result) }
        }
    }

    /** Forget the server and the token, and stop observing the screen. */
    fun disconnect() {
        settings.clear()
        repository.resetSession()
        _state.update {
            it.copy(
                serverUrl = "",
                authToken = "",
                screenObservation = false,
                testResult = null,
            )
        }
    }

    companion object {
        fun factory(
            settings: SettingsStore,
            repository: AuraRepository,
        ): ViewModelProvider.Factory = object : ViewModelProvider.Factory {

            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T =
                SettingsViewModel(settings, repository) as T
        }
    }
}
