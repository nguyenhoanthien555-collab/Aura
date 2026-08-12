package com.aura.companion.data.settings

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Settings held in memory, for tests.
 *
 * The real [SettingsStore] needs a `Context` and a Keystore-backed key, so
 * it cannot exist on a JVM. This supplies the same values through the same
 * read-only interface.
 *
 * One `MutableStateFlow` backs both members, mirroring the real store: a
 * test that assigns `current` also moves `settings`, so a collector cannot
 * be told a different story from a caller reading the value directly. Two
 * independent fields would let them disagree, and a test that passes
 * against a fake behaving unlike the store proves nothing.
 *
 * `current` is settable because a test needs to change a token between two
 * requests to prove the interceptor re-reads it - the behaviour that makes
 * a token edited in Settings take effect on the next message rather than
 * the next app launch.
 *
 * The [DeviceSettings] mutators are here for the hub's ViewModel, which owns
 * the settings screen and therefore writes as well as reads. They go through
 * the same flow, so a test can assert that a toggle the user flipped is what
 * the next request sees.
 */
class FakeSettings(
    serverUrl: String = "",
    authToken: String = "",
    deviceId: String = "android-test",
    screenObservationEnabled: Boolean = false,
    notificationsEnabled: Boolean = true,
    uploadScreenshots: Boolean = false,
) : DeviceSettings {

    private val _settings = MutableStateFlow(
        AuraSettings(
            serverUrl = serverUrl,
            authToken = authToken,
            deviceId = deviceId,
            screenObservationEnabled = screenObservationEnabled,
            notificationsEnabled = notificationsEnabled,
            uploadScreenshots = uploadScreenshots,
        )
    )

    override val settings: StateFlow<AuraSettings> = _settings.asStateFlow()

    override var current: AuraSettings
        get() = _settings.value
        set(value) {
            _settings.value = value
        }

    override fun setScreenObservation(enabled: Boolean) {
        current = current.copy(screenObservationEnabled = enabled)
    }

    override fun setNotifications(enabled: Boolean) {
        current = current.copy(notificationsEnabled = enabled)
    }

    override fun setUploadScreenshots(enabled: Boolean) {
        current = current.copy(uploadScreenshots = enabled)
    }

    override fun setThemeMode(mode: ThemeMode) {
        current = current.copy(themeMode = mode)
    }

    override fun setDynamicColour(enabled: Boolean) {
        current = current.copy(dynamicColour = enabled)
    }
}
