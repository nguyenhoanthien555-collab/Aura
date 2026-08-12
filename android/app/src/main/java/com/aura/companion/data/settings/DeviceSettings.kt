package com.aura.companion.data.settings

/**
 * The settings that belong to this phone, readable and writable.
 *
 * [SettingsProvider] is deliberately read-only, and it stays that way: the
 * repository, the stream client and the chat screen have no business turning
 * screen observation on. But the hub *is* the settings screen, so it needs
 * both halves, and depending on [SettingsStore] for the writing half is what
 * kept its ViewModel off the JVM entirely - the store needs a `Context` and a
 * Keystore-backed key, so a plain unit test dies in the constructor before
 * reaching anything worth asserting on.
 *
 * Naming the write surface rather than widening [SettingsProvider] keeps the
 * answer to "who may change this phone's settings" a short list: this
 * interface is implemented by [SettingsStore] and by the test double, and
 * depended on by the hub alone.
 *
 * Only device settings appear here. Anything the *server* owns is changed
 * through `PATCH /api/settings` and is not a local write at all.
 */
interface DeviceSettings : SettingsProvider {

    fun setScreenObservation(enabled: Boolean)

    fun setNotifications(enabled: Boolean)

    fun setUploadScreenshots(enabled: Boolean)

    fun setThemeMode(mode: ThemeMode)

    fun setDynamicColour(enabled: Boolean)
}
