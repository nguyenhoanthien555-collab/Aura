package com.aura.companion.data.remote

import com.aura.companion.data.settings.SettingsProvider
import kotlinx.serialization.json.Json
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Response
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import java.util.concurrent.TimeUnit

/**
 * Builds the HTTP client.
 *
 * Rebuilt whenever the server URL changes, because Retrofit's base URL is
 * fixed at construction. The OkHttp *client* is shared across rebuilds so
 * the connection pool, and therefore established TLS sessions, survive a
 * settings change.
 *
 * Timeouts are deliberately generous on read. A free-tier host suspends
 * an idle service and takes tens of seconds to wake, and a first message
 * after a long gap has to survive that - a 10-second read timeout would
 * turn every cold start into a failure the user has to retry manually.
 */
object ApiFactory {

    /** Long enough to cover a free-tier cold start. */
    private const val CONNECT_TIMEOUT = 30L
    private const val READ_TIMEOUT = 120L
    private const val WRITE_TIMEOUT = 60L

    val json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
        isLenient = true
    }

    /**
     * One client for the process.
     *
     * `retryOnConnectionFailure` covers the Wi-Fi-to-mobile handover: the
     * socket dies, OkHttp opens a new one on the new interface, and the
     * request completes without the UI ever seeing an error.
     */
    private val client: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(CONNECT_TIMEOUT, TimeUnit.SECONDS)
            .readTimeout(READ_TIMEOUT, TimeUnit.SECONDS)
            .writeTimeout(WRITE_TIMEOUT, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }

    fun client(settings: SettingsProvider): OkHttpClient =
        client.newBuilder()
            .addInterceptor(AuthInterceptor(settings))
            .build()

    fun create(settings: SettingsProvider, baseUrl: String): AuraApi =
        Retrofit.Builder()
            .baseUrl(baseUrl)
            .client(client(settings))
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(AuraApi::class.java)
}

/**
 * Attaches the bearer token to every request.
 *
 * Read from the store per request rather than captured once, so a token
 * changed in Settings takes effect on the next message instead of the
 * next app launch.
 *
 * No logging interceptor is installed, in any build type. A body logger
 * would print the Authorization header, and "only in debug" is how a
 * token ends up in a bug report.
 */
private class AuthInterceptor(
    private val settings: SettingsProvider,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {

        val token = settings.current.authToken

        if (token.isBlank()) return chain.proceed(chain.request())

        val authorised = chain.request().newBuilder()
            .header("Authorization", "Bearer $token")
            .build()

        return chain.proceed(authorised)
    }
}
