package com.aura.companion.data.settings

import kotlinx.coroutines.flow.StateFlow

/**
 * Read access to the user's configuration.
 *
 * Consumers need to know what the settings are and when they change. They
 * do not need to change them, and they have no business knowing that the
 * real implementation lives in the Android Keystore.
 *
 * That distinction is what makes the layers above testable.
 * [SettingsStore] cannot be constructed off-device: it needs a `Context`
 * and a hardware-backed key, so a plain JVM test that touches it fails in
 * the constructor before reaching anything worth asserting on. Depending
 * on this instead lets the request building, the auth header, the error
 * mapping and the send lifecycle be tested for real, which is where the
 * bugs in them actually are.
 *
 * Deliberately read-only. The mutators stay on [SettingsStore], because
 * "who is allowed to turn screen observation on" is a question with
 * exactly one correct answer - the settings screen - and widening this
 * would make it a question with several.
 */
interface SettingsProvider {

    /** The configuration as of now. */
    val current: AuraSettings

    /** The same value, as it changes. */
    val settings: StateFlow<AuraSettings>
}

