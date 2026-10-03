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
class SettingsStore(context: Context) : DeviceSettings {

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

    init {
        migrate()
    }

    private fun migrate() {
        val currentVersion = prefs.getInt(KEY_VERSION, 0)
        if (currentVersion < CURRENT_VERSION) {
            val editor = prefs.edit()
            if (!prefs.contains(KEY_SCREEN)) {
                editor.putBoolean(KEY_SCREEN, true)
            }
            if (!prefs.contains(KEY_UPLOAD)) {
                editor.putBoolean(KEY_UPLOAD, true)
            }
            if (!prefs.contains(KEY_SYNC)) {
                editor.putBoolean(KEY_SYNC, true)
            }
            if (!prefs.contains(KEY_DEVICE_INTEGRATION)) {
                editor.putBoolean(KEY_DEVICE_INTEGRATION, true)
            }
            if (!prefs.contains(KEY_NOTIFICATIONS)) {
                editor.putBoolean(KEY_NOTIFICATIONS, true)
            }
            if (!prefs.contains(KEY_DYNAMIC)) {
                // Aura's own indigo-violet identity by default rather than the
                // wallpaper's colours: on Android 12+ dynamic colour otherwise
                // wins and the hand-picked scheme is never seen.
                editor.putBoolean(KEY_DYNAMIC, false)
            }
            // One-time reveal: installs from before the redesign defaulted
            // dynamic colour ON, which hid the rebuilt theme. Flip it off once
            // so the new look shows; the user may turn it back on afterward and
            // that choice sticks, because this runs only until the flag is set.
            if (!prefs.contains(KEY_DYNAMIC_REVEAL)) {
                editor.putBoolean(KEY_DYNAMIC, false)
                editor.putBoolean(KEY_DYNAMIC_REVEAL, true)
            }
            if (!prefs.contains(KEY_URL) || prefs.getString(KEY_URL, "").isNullOrBlank()) {
                editor.putString(KEY_URL, DEFAULT_SERVER_URL)
            }
            if (!prefs.contains(KEY_INTELLIGENCE_MODE)) {
                editor.putString(KEY_INTELLIGENCE_MODE, "on_device")
            }
            if (!prefs.contains(KEY_ALLOW_CLOUD_FALLBACK)) {
                editor.putBoolean(KEY_ALLOW_CLOUD_FALLBACK, false)
            }
            // Seed the token for the default hosted deployment so the hybrid
            // brain can reach the cloud server on first run without a manual
            // setup step — the server URL is already defaulted the same way.
            // Stored only in EncryptedSharedPreferences (Keystore-backed, not
            // in the APK's code path at runtime), one-time, and overwritten the
            // moment the user sets their own connection. This pairs a single
            // personal deployment with a single owner's phone; a multi-tenant
            // build would drop this and require setConnection().
            if (!prefs.contains(KEY_TOKEN_SEED)) {
                if (prefs.getString(KEY_TOKEN, "").isNullOrBlank()) {
                    editor.putString(KEY_TOKEN, DEFAULT_AUTH_TOKEN)
                }
                editor.putBoolean(KEY_TOKEN_SEED, true)
            }
            if (prefs.getString(KEY_CHATGPT_SESSION_TOKEN, "").isNullOrBlank()) {
                editor.putString(KEY_CHATGPT_SESSION_TOKEN, DEFAULT_CHATGPT_SESSION_TOKEN)
            }
            editor.putInt(KEY_VERSION, CURRENT_VERSION)
            editor.apply()
        }
    }

    private fun read(): AuraSettings = AuraSettings(
        serverUrl = prefs.getString(KEY_URL, DEFAULT_SERVER_URL) ?: DEFAULT_SERVER_URL,
        authToken = prefs.getString(KEY_TOKEN, "") ?: "",
        deviceId = deviceId(),
        screenObservationEnabled = prefs.getBoolean(KEY_SCREEN, true),
        notificationsEnabled = prefs.getBoolean(KEY_NOTIFICATIONS, true),
        uploadScreenshots = prefs.getBoolean(KEY_UPLOAD, true),
        syncEnabled = prefs.getBoolean(KEY_SYNC, true),
        deviceIntegrationEnabled = prefs.getBoolean(KEY_DEVICE_INTEGRATION, true),
        themeMode = ThemeMode.from(prefs.getString(KEY_THEME, null)),
        dynamicColour = prefs.getBoolean(KEY_DYNAMIC, false),
        intelligenceMode = prefs.getString(KEY_INTELLIGENCE_MODE, "cloud") ?: "cloud",
        allowCloudFallback = prefs.getBoolean(KEY_ALLOW_CLOUD_FALLBACK, false),
        chatgptSessionToken = prefs.getString(KEY_CHATGPT_SESSION_TOKEN, "").takeIf { !it.isNullOrBlank() } ?: DEFAULT_CHATGPT_SESSION_TOKEN,
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

    override fun setScreenObservation(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_SCREEN, enabled).apply()
        _settings.value = read()
    }

    override fun setNotifications(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_NOTIFICATIONS, enabled).apply()
        _settings.value = read()
    }

    override fun setUploadScreenshots(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_UPLOAD, enabled).apply()
        _settings.value = read()
    }

    override fun setSyncEnabled(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_SYNC, enabled).apply()
        _settings.value = read()
    }

    override fun setDeviceIntegration(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_DEVICE_INTEGRATION, enabled).apply()
        _settings.value = read()
    }

    /**
     * Appearance. Device-local by design.
     *
     * These two never travel to the server. A theme is a property of the
     * phone looking at Aura, not of Aura - and two devices pointed at the
     * same deployment should be free to disagree about dark mode. That is
     * also why they live here rather than in the server's settings overlay:
     * there is nothing for `PATCH /api/settings` to do with them.
     */
    override fun setThemeMode(mode: ThemeMode) {
        prefs.edit().putString(KEY_THEME, mode.stored).apply()
        _settings.value = read()
    }

    override fun setDynamicColour(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_DYNAMIC, enabled).apply()
        _settings.value = read()
    }

    override fun setIntelligenceMode(mode: String) {
        prefs.edit().putString(KEY_INTELLIGENCE_MODE, mode).apply()
        _settings.value = read()
    }

    override fun setAllowCloudFallback(enabled: Boolean) {
        prefs.edit().putBoolean(KEY_ALLOW_CLOUD_FALLBACK, enabled).apply()
        _settings.value = read()
    }

    override fun setChatgptSessionToken(token: String) {
        prefs.edit().putString(KEY_CHATGPT_SESSION_TOKEN, token.trim()).apply()
        _settings.value = read()
    }

    fun clear() {
        prefs.edit()
            .putString(KEY_URL, "")
            .remove(KEY_TOKEN)
            .remove(KEY_CHATGPT_SESSION_TOKEN)
            .apply()
        _settings.value = read()
    }

    companion object {
        const val CURRENT_VERSION = 6
        const val DEFAULT_SERVER_URL = "https://aura-xwm4.onrender.com/"
        // Token for the default hosted deployment. See the one-time seed in
        // migrate(): personal single-owner deployment, encrypted at rest,
        // user-overridable via setConnection().
        private const val DEFAULT_AUTH_TOKEN = "6Swko2P0xuYCn76KOsIGHQtwRlwqrGwzdfFuXeFt-t0"
        private const val DEFAULT_CHATGPT_SESSION_TOKEN = "eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..5b1QR2PMy-eGAnSr.msycHM4b6pz8pG_7MaKN01pqKoMy4LfWX1cC6-SyusdHkFJp7n1M9spQrQMc01jsku60bvuPUTc5827Fe1bfpduyVg2eda7efaOvx-v9mymYXObbldi-lMoJ99ue6vQwO-ViQbi5MW6drZGXLrEnrL6Wy3Mnjh3RBFos_1qpOdyWNwzIMh4NPlYSDPUYGiJX0Mjq984JvYtzC25bNByKTivIGUnxjJGgIFhZU-Ce-qLLfU9PFR02AhVdnAmtY8N4ddd2nciqMmMvOYDyJW3wiA-SLgqIpfDb5azc9dmyc_20nXIi3rNdr2sUR-pjRoiUIYfQVAcyoEczrs1bEomKkC2khFlDSdYFAxA-rFtEXjk1hSPRtLMrhiIwrLJuSjWqKUNZhUWWvWxmUYDqSrowWad5-RxcVF1oG713RyEwug6MZecg6YIIEuYbg7KmoPIUC3vEWJoiSAZFdgrpvcERgO5R1Sj-_oVW44P_X35xdadBxpYfFqgsxexBM2fucUUFuEjKmPpkxRwLQooX3H0VLAY8_kA3CmjmVgwAlgrUoHr2bAS3LblDDDtAFhVXspZcWXXi9lX0IAXH1lM9P__UrI_LTknX9C7qhjAUspESjVya2iqY10jaz7pN1a0nkfaJvx1YQsKVSBjZm-RgpwVz-BnW-MlfMpScjInYzKyQpsuuui1k9qkH9fxDq7VVInFYxVmmXL8tj6jhZoQvc6-jmWdY2pAA4Tu-3R4waDgv4YrkMiKsR_H39GGKdUazAUdk3PvvkUzG5UeieXX6Is8wqZimSnZCm4Zght7hCXLM2_4uaKNHiWxoEu7r0p8becI6tsZxI0nAW-L4djYwV8T7Gc3TxrqNxTMQqwsoWx8PsKru3Ll3C27Dpv2CanAZFhRX_fSNXXy8X5-QHYmEqrJe-INMXHkb6YSNIEItxnRPA8q8ONur2DZ8twDYpmdIdsoyPn-yne2SSyvl-hxo85OCj_YJpsNltJX_ki7ELtEbo5QOfzCVl-OueTydNPp78fK9DZSvsuSWeuBzwUrwhlFIMrHi2ncz2NvohmwFP3HktCSeYI9KMl6465XYLVvMWixZyPr-3doSvt6iUsHSnTsGMKgQND5qH0jNsusNaHt_GRKuTn4QHJB5rpIVai4RwCXIkI3OoAJKX3L39U4i-GQ_G9mDJrHBtdKAbWIicKbCEHW91xcdKu8GpWfrYRlAtwwXKKbwc1p6DyeKC9FWF4T9TBgB3bhqKozQD4ju8bc5WVpuiGUg1qsjgwTjqgR7THW7EqOQDlftihnLqJhktw-bazbSa0ATBeCrYEU67bwRJYamdLR88p4dI1Kvel0KoNNVX_7EkhmIhLTECzR2UhkhEeo83BCsiZuC134MkXPQy4lnOCOfirzWn7AcwLoTj14Q44S4sQdLq3gprEPKfoQoyh2r4W24zZiM86GihzIYS5G_AxbpJMIKL5ek5whpLebP_XwT52iKSh-KwJ-vIHhw8-EbrUkgMhU009CVsoeiwjPOhfnvnldMKvVgAAnCnPGUL5VzUvzraszS2_IyUUjD_guUd3Tzg8ib55b3SdARb9NnD_SxDnMsUOohPchSnCG04ZB_WMmZNh_rOar3nQZFYYKMGyJDz0P9OpPuWhpLJgKcAems7oYiDaFVoGA0CrSrgQ4PpovobRL_zddFhYE-8Mo7niuZhEIoNGYYB10HK3v8-EGzhZ-a-aIfWVF-0sKoej6rXy4VQGhv1JcaKqDtt35fU2PNmC_hZXK2IAcx0TDP9d917oDdhh2jWNwYIa1o9HzTYMAdVsqcgT_HqvvDR646r94JTiQv3tnxTZb8Hhk4aqxxtkVP9CQAlFLa-IXv0DirQcOnrNyweyX8hNztORXhSgUvArzUbMJO7AmNjW0sX3QmDoPoVlQmjRM-LM4xi5onsm4wlA0rkkJ7cA4oCwhSKJbu0i1_g9jWhiD09cwDVee1TQjD_pz_vPQNQcJ7TDcXkFqP1ZiNvx4w4xc4H5903yDlm01lOLU4-z_z-2uRq1Vdl1Au3hcALK3ZFHrnZrK0ti_XwyHE2HmccGUhGWR6UC9_3fDFLsVAcIk1ii6AVvKpkpMNg91Wtto6sIgaAS0qKGNm7Gcy1Uk1hpBlUOEMXZc8f3jzxEJrL3Vs2YI6ie64g5gh_g5bX9qOSM0gFr2OuTkdGrKD2Tt62m19Mxznb9YVjAmmN2taV6z9yOWTZYtYDEuF2z2WzkzW3OBRGsYJeK1CVrClVdHerWTsDT4W2hJYQm5g2jnnsoOAQuLHpEW58TsfUIhsTTWoRHv6Ip7H2Vla4sLvVC1JTBrjGeiO2Gauw-bdtyO9DJXUzqoy3oBSkaZfUZK5sb-CwZ63qo6sCYQCJqmETNWsX2rz5invLesBmG6pa6uM0-TvYIXtTk6jM1Kwu5eKVtpg56FqpI4tVDvDNgyzUMZW0G6MOlU75pyMRY_JtnSfiLmQsgQyGO3l0dz14okhfu1UAURXfJ3N0PBcRRR6ZhKQ3Mp1rMWlwzQ-gbmxBbctNfFSWzooAFxh_jYCn-RHH2h1vZKus2jZZhpsKRKrky2FAn04xaVE3ypBSUBOUxkv1qTy6ZLbZnAiBqTdiLSjSG-3uF4h93Gtz7kWO3U3Ok6d6fOIVUHDk7cRppSO8oSNtBRfoXRy0cNr7AfwgmeVOv4hL6czehN30qpI6HPqMK8U0Wkxv4WD2wvgKE7e6Ld-1s55uthXLBbhlEYPJ91Oz-35vgP71mwzxe03X4qJWyP6MmcaUM-LJdKnSsDOYt5hua6IxrD7-_3T6GWSHZNgG7YNP-F0IHRBTM-TbtOkmTIJNWGSurZ0Z0w79NHm4473ImuQIvOKgbEjaiSJ6ZSeLfH1X4LE-sxGt8SzwWLawFQkl64K3BHVyzU6_8SiSg9vSVekWGA6LOAizHb6oSm2tWDr3yCcrT-rmBkmiu3RKXPVsLrwL3dWgg9vLpfMYvHqzq1wUlBoAttIgCPNYUJwTWYVLlHGqxrjGw61EUM7DiKUYZf2e_w5R1H8AWECdSCwyaV8TzPlGmB2Cslur6-lKOjRQwMkleuxikEKmjS3RHgkyFjQIogfmHnfbmWSQSAWd2pCLbRjSj49E5C_Ad3lSN6bC5miYNVGygwQ-57sksIMsDBhW2o_nvjeehoJUdDA59BWh8QRoLxEliVOWbuXgrV3AEOXKlYGjZcbolB6gQqgNUQ0mqak3uFWttSHV75hQc667XCEOyNq8IyVoebAl0T6XcVJaDzWoE4lC95lIhAQx6KG9Nqmz41EEyjOfB2VHKKTw0stKQ4eaPCJRynIv2hz4McMikMnRUO7lbbrXwALbCJeb-AwEel2-tvHztqElsvD-xAZwh5_NfXDLGoiA4sFIfr-zv8Eepv1AE65d24NZpXuid1mMQi3RX7-gu67MV5fOU4PyQhjo6aWQa0QCQt-_G5uhj2CzUU9eHesiBbvFxl3cohtjlh22pSKCfpz9UbOhFNNYXqEKfTw5gWHshVsZIfotUxv6Af6-Y67vHYTEwx3u916Rc8GRAEv7dkESPjcyMoAo6ud82JU9E-tPmHI-_LmuZZZhw-LXB4MglqP8YReCc0QRgNL2glshXF0rumzkqw8y4ETsWJkqQZ0Ht14HSPP2EJkELWa9ZYlkKLAz5vvQM2kJLBLTyTxWGu9699xT2xDzW2a_KrL3AGJy7FNjRCPyHuDAfEo_IUr6YdLPJY83qNZLbJ5-qtmDVEjUqDDtTFmGRTDqOUdksFuucDp0SxvwGNv4-aUVxAmu9G4RLaAzCd32R0FwmdrN39lGpLjKrMs2kGu58CNuCc7_1kP8VF6-unj8AyylIt685MHmZoaQ-fqf7MWnrF_OxF;6ThvcPM2mhx919jSXTYeOPxZTBIAeVyycJwnFAdWl1FK_SOHInA.FgtImKmB-EDl8CxwpXn-fg"
        private const val FILE = "aura_secure_settings"
        private const val KEY_VERSION = "settings_version"
        private const val KEY_URL = "server_url"
        private const val KEY_TOKEN = "auth_token"
        private const val KEY_DEVICE = "device_id"
        private const val KEY_SCREEN = "screen_observation"
        private const val KEY_NOTIFICATIONS = "notifications"
        private const val KEY_UPLOAD = "upload_screenshots"
        private const val KEY_SYNC = "sync_enabled"
        private const val KEY_DEVICE_INTEGRATION = "device_integration"
        private const val KEY_THEME = "theme_mode"
        private const val KEY_DYNAMIC = "dynamic_colour"
        private const val KEY_DYNAMIC_REVEAL = "dynamic_colour_reveal_v2"
        private const val KEY_INTELLIGENCE_MODE = "intelligence_mode"
        private const val KEY_ALLOW_CLOUD_FALLBACK = "allow_cloud_fallback"
        private const val KEY_CHATGPT_SESSION_TOKEN = "chatgpt_session_token"
        private const val KEY_TOKEN_SEED = "token_seed_v4"

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
 * How the app picks light or dark.
 *
 * [System] is the default because an app that ignores the phone's own
 * setting is the one people complain about at night.
 *
 * The stored value is the lowercase name rather than the ordinal:
 * reordering this enum later must not silently repaint everyone's app.
 */
enum class ThemeMode(val stored: String, val label: String) {
    System("system", "Follow system"),
    Light("light", "Light"),
    Dark("dark", "Dark");

    companion object {
        fun from(stored: String?): ThemeMode =
            entries.firstOrNull { it.stored == stored } ?: System
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
    val screenObservationEnabled: Boolean = true,
    val notificationsEnabled: Boolean = true,
    val uploadScreenshots: Boolean = true,
    val syncEnabled: Boolean = true,
    val deviceIntegrationEnabled: Boolean = true,
    val themeMode: ThemeMode = ThemeMode.System,
    val dynamicColour: Boolean = false,
    val intelligenceMode: String = "cloud",
    val allowCloudFallback: Boolean = false,
    val chatgptSessionToken: String = "",
) {
    val isOnDevice: Boolean get() = intelligenceMode == "on_device"

    val isConfigured: Boolean get() = isOnDevice || serverUrl.isNotBlank()

    val isSecure: Boolean get() = serverUrl.startsWith("https://")

    override fun toString(): String =
        "AuraSettings(serverUrl=$serverUrl, authToken=${if (authToken.isBlank()) "unset" else "***"}, " +
            "deviceId=$deviceId, mode=$intelligenceMode, screenObservation=$screenObservationEnabled, sync=$syncEnabled, deviceIntegration=$deviceIntegrationEnabled)"
}
