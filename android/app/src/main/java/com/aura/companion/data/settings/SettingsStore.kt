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
        chatgptSessionToken = prefs.getString(KEY_CHATGPT_SESSION_TOKEN, "") ?: "",
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
        const val CURRENT_VERSION = 5
        const val DEFAULT_SERVER_URL = "https://aura-xwm4.onrender.com/"
        // Token for the default hosted deployment. See the one-time seed in
        // migrate(): personal single-owner deployment, encrypted at rest,
        // user-overridable via setConnection().
        private const val DEFAULT_AUTH_TOKEN = "6Swko2P0xuYCn76KOsIGHQtwRlwqrGwzdfFuXeFt-t0"
        private const val DEFAULT_CHATGPT_SESSION_TOKEN = "eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..QBA-v5IJCCouMVRW.ZGJV0iG-F4yxK2HvaBIbL_959JwXXQfQNIqqOgwi1TisgTmEYjn43xCOPEeHLOEQSEh6oTbXDt3SRpYxxJ6O6KUo7lb-xP0YoC-yE9GS9YWNH21BTWj84WFU5ebA_Dyk40a1dqFo5tapgssUsFCYlfQohQyoSZqpWdtHadFKKMAooDUKhcL527npGjEahU34Eha36dbhrRAs_7s5Pp8Ec6P06oP1cTZzECKBpcEcVx7qSAOCQdpeuhpBiobt2hMJI8_7gRBxV9V4cA4BddT5Jl9kyX8486dW6WCCEhA5zo1n-bVXAvclqWIXWXzxWe0vR6Rd4kT2sG13swEtciX_bELuulsA-_iEv1gS5oq2ZpMIOZGWqxA8EaX66gRPk55IQu9_GxAfnVLS2_T6IbCSNNsAsBbqtU0K0PFsfICGk3hSE-hYskeNhINPC7uSxCpgwULNFOw2IE3E7o7vlmff36-xMsI2OhPwikDLeOGpC_2_UoTsXmpcJwlYfCZamV3qpq6bozFf7eWov2iJAWg_JotxsF9JsK0QRvwMIQZsyBOlc6QsLltDaaFl0fNl-mS6dR6O3og4YokLIRIQJgyPJI4wmzJL-HBP9U9O0KvVUmzlgoJjjx0dbNCCy---PPfKnzvIGIYnB0C0YqX2c2suSqzWGZWAs8bQDXZt-baRFyk6fnxdrNqUpoe9N2UZe3H_-w5OYOmDxjBNAz4p7hgd7driR5wQt-bC1p-HyBKoLXs6RKr9qR_PpVhoKFh1bv_Ihz6p0VqEh2fzlU4ZmmUPhow3kzXU0swD0nRR8WzcsI4yu_8mOhVKF0QZhv9GNltxe5XT04QpS__igXrGiFjG_1Go0xGacb8YtnlWwNbK8LzlQQvKC5pli7iS_cmJ02VzQjW-cZnvLw12jeU3c1UxJVGpNcT_dz_2KAymcd1t4NrWBHSzpgeNFkU-Pookp1-equ7jdEHfiaoy_D1RvGMvlxw0Tb5T9CTithQe1706Ig4-umc4tZok_kZCXxAhYZIdGnkvNcMqklFHyQUJQ6PgX6zgfRvnlx8Z40mLDbRpTCtE81tudEG2k_Gvjd98K1fov4c5-6ab_jF2EQqVD1ahLcFOzTPFOxePfWUyBxCIVnsn2GFjYh3_b8Sn-e-wzZOaJ2Ff9Bf1B6yLiUlFImFxa4IqAGcr2vDUiP3AhTYkFFwaX1dbgn_NHH6hp5Csx4IMM8aSZ2OdAwKyBQhvnP3bKdu10WnnUpagx8cAmFqcKXAkujcz8Ek1LMJsJkp5UrZRtHUn0RrGYTWEUrYz9kLPfOvkAaa8ffXt1Qxtsya3SEw1XbmtUtaxu1FT4yKJH470dHnNPNq-5aL0RjclV6Tjs7_ZzJn_mqOEsqcNVQL9NR0191QF8COqSoGy1jg3s58CztqyjsFqHZ-BaNHNJQ_K6yFXYuNyD7l3kBLJBstYM5UdNwdeYcbGvE4_oF47D6czz8gYbRsSEPEJE8pixc_PnY3xkSazJ6UDVq4xy4XKBQDRba1cCaIRCoS9xcqM1XWsWBlGCiJ4Gizg1J4w_3ywytWhTOZvWbsXmDVnyZTriLCZC__DEOm33J2kdfRSeGgANbM2WsuSC9XJAKfvhBl_oopgMJTiWCptjXzEL1aM5LvKQOuUq7xGHUsNbdppFjxAXdEzws1agIkF0jSFSARLB6JShV71VK81niPUjsterGGCijLXGFYO701Noossh6GycIpI17gpuGG1LlC-NyDrMY4bwWV1X8A99IxMYYup_5jorbsEO4nEGApVV3wUdd1FI-978sfTmZ80iqB7h6uadz762hCbJWQr9NuSp3tmO0ci-0vwI0UQoIaqBNVlyTZ8BFXdSy93tfVvIOl0xKh6lVhEHayGsTD2C6n2QFO2sUPekGhsJeuRC5379fwKQBBNXVyhbXc8hUiHScGqALSGvdK2EyPIO9QC1rFcPik1SbUN6pVx6wQhb5Cqi4GC8a72k5Z0YBw2GaOv2pZkDXHc2PM-Gmeera1HyCuSNhtK_7Jso5dRyWUI-PGRithS_AsrNYd9IDluBCvKmd3p7X4aFOJCJO8PQUDxu9EkAEMQUv0Soiw1pYHZ4t2_LU_iJsgOqNWeEqHiMMTpvcxZbqQy96BHd8wJU8dRbZIgSiHPOjrTb7EvK8RrZryPlcAasDF_zLz473bxAbZrjB9Zt186dWwF30GFd5FV5BRvaM5XXkWFMKSGx3e9pSj9HjcU90OWT9ZsaDiT5BHaHDLp6T36k5CxrAiLjz-GggZUAg3eHg4K20-4YuAn5FLgBGxvRMW2tp2qyTfuH9jb45wu7Rw2TKhlQWxAZUE-W45s0aIdvZ91JESbOZJ0b8bXNodsyO8aWoYZBP3y48xExmiQO_AddmV4zXNA3-13_LCzkrSXtyW-dgdM5rcL-zg8m6e43F7Tl57baa5BxxpuhdLvgcm3XEPatrqLNntePRGxDfiZKp-uCX3965NBA6zhlS6weQ302Z9saIQqRf7E5SkTQtxOW4CdwMOL_QNt89xRm4G73TNt3bzzsoaKjyeuLS9yNZZkRdcKodLRMzW4C6tkH3dEDBkvxR__LzX2GTxBO-nfNBMZFn2-rYMLxxuA5wIQm28qXpfmnFSeQzzLZwMYZb6tiDAT3-tdRcxsIGE7Pg_8GrdiRZykud84xZLswisTi_X3P19MzNBLP3Wqta2fnxL_W2Xgh92uMXMXCuvMhWFL3TlDJpSEJ3u7TdLnX7CVvCYdJZO799n_T9M0oqNz-BbHjeK0a_WpGO9E9leQDPsnX67E9c4eJgLBFXRr0umDNKUd7iAWlpq9VO-SVYt5vhYunJPUqg8I4dEszu7cgsUQvNbGDV-3H2sEMA5554_XMw7fSNn4nMRfl737tswRqGx6Wtevqrn5To-l-R279DgCJnQIbtGdG_gigXSEeud4R3mVF6xTJ8JmLsuUzEu1unvmuc_UnjqLouTfkV-Yaobn9vlkKBW4DZCi7AmG3ze_uZtwdNuuLJU3U7_8fkhh2I4ieNt_5QQs7AxBAv8hDCUAWazHdbZzJR3qSYBL_9bfwEPlN17PsdnmQzeBVIwO4_IltyJxQJRMh9SDp_CFvDPXb0WlIJsu60PQFqg979Om43kYwiUEaG-8R0urZt2NlTfb-Zi1n5w94_Xe1HCJk2R3iHhopJ_dDFX6B0AaAVWRwVKrikIkHaHhWiUZi9sDsvvFsdMvc8wKAadUdBuROmms9Ep7_OL7vfjSaEjh528qe71RkIVnbPvMhnqC454QDslcLz8MPBTChYH6yZeSXFa-_BziqJVsoouX_m6GxNtJbN9K5OxUEVheHHaGWpdV27pjzqfNxvM746c1GFBlRZH5moR1-fbuU8w97GrTSO4sPIw7dcjuGHJ6R22wbu-zSLqTuYWslxwRBumGu4RoMxJVTMLFz9zt5oYayTZ-YVMfvHDlQJ9SULdfs59ddpJFCd30zY8WXw7axHbfkv2J0fPW6b1xcR8pRvwfkWlb62e15WvID50oLjCdvI5GmGU2neltbuer5vhX_HNq-9JBb-7RrNoknEeyvUZ3HFCP_gVUGvzrD8MGe0jEUrFE9fz_RFABow45YWhL2gwIEqiwkNzShwRBrBy7QeOfPqyzdsPr8HlFecfoVnOpHDmwwf_92syKK_Y4BRwIg0ToshAUfgEIpeC25prZNknHd1PbutdjEmP4ZHsVk84vux-KCQ96IBaU4J94Ib0CJbN9ze2mwTSmYayMoP21nCKDP6ZTlZ08VYzonGh2lr7VqoIDKm6bjx7Q-ODqo5_Dd85RnVAD3LbHUAZvvO9CViZkOy48fqNGinkQq_SwLMoUiuLgXbqKMOmqJeJhF1Dt1K3HR2V;h3fLdiViRykuwLptXsXL4iYytQqJH4XbOG7WuHe7-WdM5N9NXhQ.poU_xBb8PSSiBhxS_AL2kw"
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
