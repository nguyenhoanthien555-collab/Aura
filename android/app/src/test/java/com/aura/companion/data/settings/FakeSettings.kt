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
 */
class FakeSettings(
    serverUrl: String = "",
    authToken: String = "",
    deviceId: String = "android-test",
    screenObservationEnabled: Boolean = false,
    notificationsEnabled: Boolean = true,
    uploadScreenshots: Boolean = false,
) : SettingsProvider {

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
}
