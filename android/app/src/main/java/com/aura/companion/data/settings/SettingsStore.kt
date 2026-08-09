package com.aura.companion.data.settings

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Where the server URL and the bearer token live.
 *
 * The token is a credential, so it goes in EncryptedSharedPreferences -
 * backed by a key in the Android Keystore, which means it is not readable
 * from a backup, from another app, or from an adb pull on an unrooted
 * device.
 *
 * Neither value is ever compiled into the APK. A user types them once at
 * first run, which is also what lets the same APK point at a laptop today
 * and a cloud URL tomorrow without a rebuild.
 *
 * Nothing here is ever logged. `toString` is overridden away from the
 * default for exactly that reason.
 */
class SettingsStore(context: Context) : SettingsProvider {

    private val prefs: SharedPreferences = create(context)

    private val _settings = MutableStateFlow(read())
    override val settings: StateFlow<AuraSettings> = _settings.asStateFlow()

    override val current: AuraSettings get() = _settings.value

    private fun create(context: Context): SharedPreferences {

        val key = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()

        return EncryptedSharedPreferences.create(
            context,
            FILE,
            key,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    private fun read(): AuraSettings = AuraSettings(
        serverUrl = prefs.getString(KEY_URL, "") ?: "",
        authToken = prefs.getString(KEY_TOKEN, "") ?: "",
        deviceId = deviceId(),
        screenObservationEnabled = prefs.getBoolean(KEY_SCREEN, false),
        notificationsEnabled = prefs.getBoolean(KEY_NOTIFICATIONS, true),
        uploadScreenshots = prefs.getBoolean(KEY_UPLOAD, false),
    )

    /**
     * A stable per-install identifier, so the server can address
     * notifications at this device.
     *
     * Generated locally and never derived from a hardware ID: an
     * advertising ID or IMEI would identify the *person* across apps,
     * which is more than "which phone asked" requires.
     */
    private fun deviceId(): String {

        prefs.getString(KEY_DEVICE, null)?.let { return it }

        val generated = "android-" + java.util.UUID.randomUUID().toString().take(12)

        prefs.edit().putString(KEY_DEVICE, generated).apply()

        return generated
    }

    fun setConnection(serverUrl: String, authToken: String) {
        prefs.edit()
            .putString(KEY_URL, normaliseUrl(serverUrl))
            .putString(KEY_TOKEN, authToken.trim())
            .apply()
        _settings.value = read()
    }

    fun setScreenObservation(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_SCREEN, enabled).apply()
        _settings.value = read()
    }

    fun setNotifications(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_NOTIFICATIONS, enabled).apply()
        _settings.value = read()
    }

    fun setUploadScreenshots(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_UPLOAD, enabled).apply()
        _settings.value = read()
    }

    fun clear() {
        prefs.edit()
            .remove(KEY_URL)
            .remove(KEY_TOKEN)
            .putBoolean(KEY_SCREEN, false)
            .apply()
        _settings.value = read()
    }

    companion object {
        private const val FILE = "aura_secure_settings"
        private const val KEY_URL = "server_url"
        private const val KEY_TOKEN = "auth_token"
        private const val KEY_DEVICE = "device_id"
        private const val KEY_SCREEN = "screen_observation"
        private const val KEY_NOTIFICATIONS = "notifications"
        private const val KEY_UPLOAD = "upload_screenshots"

        /**
         * Accept what a human would type.
         *
         * `192.168.1.10:8000` is a URL to a person and not to OkHttp, and
         * a missing trailing slash silently breaks Retrofit's relative
         * path resolution. Both are fixed here rather than in a
         * validation message.
         */
        fun normaliseUrl(raw: String): String {

            var url = raw.trim()

            if (url.isEmpty()) return ""

            if (!url.startsWith("http://") && !url.startsWith("https://")) {
                // Assume TLS unless it is obviously a private address.
                url = if (looksLocal(url)) "http://$url" else "https://$url"
            }

            if (!url.endsWith("/")) url = "$url/"

            return url
        }

        private fun looksLocal(url: String): Boolean {
            val host = url.substringBefore("/").substringBefore(":")
            return host == "localhost" ||
                host == "10.0.2.2" ||
                host.startsWith("192.168.") ||
                host.startsWith("10.") ||
                host.startsWith("172.") ||
                host.endsWith(".local")
        }
    }
}

/**
 * The user's configuration.
 *
 * `toString` is overridden so a token cannot reach a log through an
 * accidental interpolation of the whole object - the single most common
 * way credentials end up in logcat.
 */
data class AuraSettings(
    val serverUrl: String = "",
    val authToken: String = "",
    val deviceId: String = "",
    val screenObservationEnabled: Boolean = false,
    val notificationsEnabled: Boolean = true,
    val uploadScreenshots: Boolean = false,
) {
    val isConfigured: Boolean get() = serverUrl.isNotBlank()

    val isSecure: Boolean get() = serverUrl.startsWith("https://")

    override fun toString(): String =
        "AuraSettings(serverUrl=$serverUrl, authToken=${if (authToken.isBlank()) "unset" else "***"}, " +
            "deviceId=$deviceId, screenObservation=$screenObservationEnabled)"
}
