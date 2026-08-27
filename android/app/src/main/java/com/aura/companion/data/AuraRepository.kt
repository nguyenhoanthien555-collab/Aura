package com.aura.companion.data

import com.aura.companion.data.remote.ApiFactory
import com.aura.companion.data.remote.ApiKeyRequestDto
import com.aura.companion.data.remote.ApiKeyResponseDto
import com.aura.companion.data.remote.AgentRunSnapshotDto
import com.aura.companion.data.remote.AgentStepRequestDto
import com.aura.companion.data.remote.AuraApi
import com.aura.companion.data.remote.ChatRequestDto
import com.aura.companion.data.remote.DevicePollRequestDto
import com.aura.companion.data.remote.DevicePollResponseDto
import com.aura.companion.data.remote.DeviceCapabilityStatusDto
import com.aura.companion.data.remote.DeviceResultAckDto
import com.aura.companion.data.remote.DeviceResultReportDto
import com.aura.companion.data.remote.DeviceResultSubmissionDto
import com.aura.companion.data.remote.DecisionDto
import com.aura.companion.data.remote.HealthDto
import com.aura.companion.data.remote.NotificationDto
import com.aura.companion.data.remote.AuraStreamClient
import com.aura.companion.data.remote.ProviderHealthDto
import com.aura.companion.data.remote.ProviderTestRequestDto
import com.aura.companion.data.remote.ProviderTestResponseDto
import com.aura.companion.data.remote.ProvidersResponseDto
import com.aura.companion.data.remote.ScreenRequestDto
import com.aura.companion.data.remote.SettingsPatchDto
import com.aura.companion.data.remote.SettingsPatchResponseDto
import com.aura.companion.data.remote.SettingsResetRequestDto
import com.aura.companion.data.remote.SettingsResetResponseDto
import com.aura.companion.data.remote.SettingsResponseDto
import com.aura.companion.data.remote.StreamEvent
import com.aura.companion.data.chat.SessionStore
import com.aura.companion.data.settings.SettingsProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaTypeOrNull
import okhttp3.MultipartBody
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import retrofit2.Response
import java.io.File
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.concurrent.atomic.AtomicReference

/**
 * The app's single door to the Aura server.
 *
 * Everything above this line - ViewModels, Composables, the accessibility
 * service, the notification worker - talks to this. Nothing above this
 * line touches Retrofit, OkHttp, an HTTP status code or a `Response`.
 *
 * The repository also owns the session id, which is what makes a
 * conversation continuous: the server generates one on the first reply
 * and every later message carries it back.
 */
class AuraRepository(
    private val settings: SettingsProvider,
    private val session: SessionStore = SessionStore.None,
) {

    private val cached = AtomicReference<Pair<String, AuraApi>?>(null)

    private val streamClient = AuraStreamClient(settings)

    /**
     * The conversation the server knows about, restored if there is one.
     *
     * Defaults to [SessionStore.None], which remembers nothing - so a caller
     * that passes no store behaves exactly as this class did before §15, and
     * every existing test still describes real behaviour.
     *
     * Read defensively. A stored session id is a convenience for the next
     * launch, and a Keystore that has become unavailable must not stop the
     * app from starting a conversation now.
     */
    private val _sessionId = AtomicReference<String?>(
        try {
            session.sessionId
        } catch (error: Exception) {
            null
        }
    )

    val sessionId: String? get() = _sessionId.get()

    fun resetSession() {
        _sessionId.set(null)
        remember(null)
    }

    /**
     * The API for the currently configured URL.
     *
     * Rebuilt only when the URL changes. Retrofit fixes its base URL at
     * construction, and rebuilding per request would discard the
     * connection pool - which on a mobile link costs a full TLS handshake
     * every message.
     */
    private fun api(): AuraApi? {

        val url = settings.current.serverUrl

        if (url.isBlank()) return null

        cached.get()?.let { (cachedUrl, api) ->
            if (cachedUrl == url) return api
        }

        return runCatching { ApiFactory.create(settings, url) }
            .onSuccess { cached.set(url to it) }
            .getOrNull()
    }

    // ------------------------------------------------------------------
    // Health
    // ------------------------------------------------------------------

    suspend fun health(): AuraResult<HealthDto> = call { it.health() }

    // ------------------------------------------------------------------
    // Agent tool protocol
    // ------------------------------------------------------------------

    /** One round of the agent loop, driven by this device. */
    suspend fun agentStep(
        request: AgentStepRequestDto,
    ): AuraResult<AgentRunSnapshotDto> = call { it.agentStep(request) }

    /** The device gateway's queued invocations, if any. */
    suspend fun pollDeviceInvocations(
        deviceId: String,
        timeoutS: Double = 0.0,
        capabilities: Map<String, DeviceCapabilityStatusDto> = emptyMap(),
    ): AuraResult<DevicePollResponseDto> =
        call { it.pollDeviceInvocations(DevicePollRequestDto(deviceId, timeoutS, capabilities)) }

    /** Structured reports for invocations this device executed. */
    suspend fun submitDeviceResults(
        deviceId: String,
        reports: List<DeviceResultReportDto>,
    ): AuraResult<DeviceResultAckDto> = call {
        it.submitDeviceResults(DeviceResultSubmissionDto(deviceId, reports))
    }

    // ------------------------------------------------------------------
    // Chat
    // ------------------------------------------------------------------

    suspend fun send(message: String, context: JsonObject = JsonObject(emptyMap())): AuraResult<ChatReply> {

        val result = call {
            it.chat(
                ChatRequestDto(
                    sessionId = _sessionId.get(),
                    message = message,
                    context = context,
                    metadata = JsonObject(mapOf("client" to JsonPrimitive("android"))),
                )
            )
        }

        return result.map { dto ->
            // Adopt whatever session the server used, so a server that
            // generated one becomes the conversation this app continues.
            adopt(dto.sessionId)

            ChatReply(
                sessionId = dto.sessionId,
                reply = dto.reply,
                messageId = dto.messageId,
            )
        }
    }

    /**
     * The same turn as [send], delivered as it is generated.
     *
     * Session continuity works exactly as it does for [send]: whatever
     * session the server names is adopted, so a reply that arrives over the
     * socket and one that arrives over REST continue the same
     * conversation. That is what lets a caller fall back from one to the
     * other without the user losing their thread.
     */
    fun stream(
        message: String,
        context: JsonObject = JsonObject(emptyMap()),
    ): Flow<StreamEvent> =
        streamClient.stream(message, _sessionId.get(), context)
            .onEach { event ->
                when (event) {
                    is StreamEvent.Started -> adopt(event.sessionId)
                    is StreamEvent.Complete -> adopt(event.sessionId)
                    else -> Unit
                }
            }

    /**
     * The one place a session id is written.
     *
     * REST used to set the field directly and only streaming came through
     * here, which was harmless while the id lived in memory alone. With a
     * store behind it a second writer is a second thing to forget, so both
     * paths now arrive here.
     */
    private fun adopt(sessionId: String) {

        if (sessionId.isBlank()) return

        _sessionId.set(sessionId)
        remember(sessionId)
    }

    /**
     * Persist the session id, or fail quietly.
     *
     * Storing it is what makes a restored transcript the same conversation
     * the server remembers. Failing to store it costs continuity at the next
     * launch; letting the failure out would cost the message being sent now,
     * which the user is watching.
     */
    private fun remember(sessionId: String?) {
        try {
            session.remember(sessionId)
        } catch (error: Exception) {
            // Nothing loggable: the id is not secret, but a failure here is
            // not actionable by the person holding the phone either.
        }
    }

    // ------------------------------------------------------------------
    // Screen
    // ------------------------------------------------------------------

    suspend fun sendScreen(
        application: String,
        packageName: String,
        screenText: String,
        accessibility: Map<String, String> = emptyMap(),
    ): AuraResult<DecisionDto?> {

        val request = ScreenRequestDto(
            sessionId = _sessionId.get() ?: "",
            deviceId = settings.current.deviceId,
            application = application,
            packageName = packageName,
            screenText = screenText,
            accessibilityContext = accessibility
                .takeIf { it.isNotEmpty() }
                ?.let { context ->
                    JsonObject(context.mapValues { JsonPrimitive(it.value) })
                },
            timestamp = System.currentTimeMillis() / 1000.0,
        )

        return call { it.screen(request) }.map { it.decision }
    }

    suspend fun uploadScreenshot(
        file: File,
        application: String,
        packageName: String,
    ): AuraResult<Unit> {

        val mediaType = "image/jpeg".toMediaTypeOrNull()

        val part = MultipartBody.Part.createFormData(
            "screenshot",
            file.name,
            file.asRequestBody(mediaType),
        )

        fun field(value: String) = value.toRequestBody("text/plain".toMediaTypeOrNull())

        return call {
            it.uploadScreenshot(
                sessionId = field(_sessionId.get() ?: ""),
                deviceId = field(settings.current.deviceId),
                application = field(application),
                packageName = field(packageName),
                timestamp = field((System.currentTimeMillis() / 1000.0).toString()),
                screenshot = part,
            )
        }.map { }
    }

    // ------------------------------------------------------------------
    // Notifications
    // ------------------------------------------------------------------

    suspend fun collectNotifications(): AuraResult<List<NotificationDto>> =
        call { it.notifications(settings.current.deviceId) }
            .map { it.notifications }

    // ------------------------------------------------------------------
    // Control Hub
    //
    // Every method here is a thin, typed wrapper. No caller above this
    // line builds a JSON body, and the one place that does - `patch` -
    // takes an already-validated map of dotted paths.
    // ------------------------------------------------------------------

    suspend fun loadSettings(): AuraResult<SettingsResponseDto> =
        call { it.settings() }

    /**
     * Change settings on the server.
     *
     * `changes` is keyed by the server's dotted paths ("proactive.enabled")
     * and nested here, because the server's allow-list is written in dotted
     * form and matching it exactly is what makes a rejection legible.
     *
     * All-or-nothing: a 422 means the server changed nothing, and the
     * message names which setting was wrong.
     */
    suspend fun patchSettings(
        changes: Map<String, JsonElement>,
    ): AuraResult<SettingsPatchResponseDto> {

        if (changes.isEmpty()) {
            return AuraResult.Failed(AuraError.Rejected("Nothing to change."))
        }

        return call { it.patchSettings(SettingsPatchDto(nest(changes))) }
    }

    suspend fun resetSettings(paths: List<String>? = null): AuraResult<SettingsResetResponseDto> =
        call { it.resetSettings(SettingsResetRequestDto(paths)) }

    suspend fun loadProviders(): AuraResult<ProvidersResponseDto> =
        call { it.providers() }

    suspend fun providerHealth(): AuraResult<ProviderHealthDto> =
        call { it.providerHealth() }

    suspend fun testProvider(
        provider: String,
        model: String? = null,
    ): AuraResult<ProviderTestResponseDto> =
        call { it.testProvider(ProviderTestRequestDto(provider, model)) }

    /**
     * Send an API key to the server.
     *
     * The key is a parameter and nothing else: it is not stored on the
     * device, not held in a field, and not logged. The server returns only
     * its mask.
     */
    suspend fun setProviderKey(provider: String, key: String): AuraResult<ApiKeyResponseDto> =
        call { it.setProviderKey(provider, ApiKeyRequestDto(key)) }

    suspend fun deleteProviderKey(provider: String): AuraResult<ApiKeyResponseDto> =
        call { it.deleteProviderKey(provider) }

    // ------------------------------------------------------------------
    // The one place an HTTP failure becomes an AuraError
    // ------------------------------------------------------------------

    private suspend fun <T> call(
        block: suspend (AuraApi) -> Response<T>,
    ): AuraResult<T> = withContext(Dispatchers.IO) {

        val api = api()
            ?: return@withContext AuraResult.Failed(AuraError.NotConfigured)

        try {
            val response = block(api)

            val body = response.body()

            if (response.isSuccessful && body != null) {
                return@withContext AuraResult.Ok(body)
            }

            // A 2xx with nothing in it. Retrofit hands back a null body for a
            // 204, and for a success whose content-length was zero - and the
            // old `else` branch turned that into "unexpected response (200)",
            // a sentence naming a success code as the failure. The route
            // answered; what it answered with is unusable.
            if (response.isSuccessful) {
                return@withContext AuraResult.Failed(
                    AuraError.Incompatible("empty body")
                )
            }

            AuraResult.Failed(
                when (response.code()) {
                    401 -> AuraError.Unauthorized
                    // Recognised, and still refused. A different instruction
                    // from "your token is wrong" - see AuraError.Forbidden.
                    403 -> AuraError.Forbidden
                    // The server validated the request and said no, in
                    // words written for this screen. See AuraError.Rejected.
                    422 -> rejection(response)
                    // The route is absent, not the server. A deployment
                    // older than this app answers chat and 404s the hub.
                    404, 405 -> AuraError.NotSupported
                    // Nothing is broken and nothing needs configuring.
                    429 -> AuraError.RateLimited
                    503 -> unavailableReason(response)
                    // A gateway that has no Aura behind it yet. On the
                    // free tiers this project targets, that is a container
                    // still starting rather than anything broken.
                    502, 504 -> AuraError.Waking
                    else -> AuraError.ServerFailure(response.code())
                }
            )

        } catch (e: SocketTimeoutException) {
            AuraResult.Failed(AuraError.Timeout)

        } catch (e: UnknownHostException) {
            AuraResult.Failed(AuraError.Offline)

        } catch (e: IOException) {
            // Covers a dropped socket mid-handover. Deliberately not
            // logged with its message: an IOException can name the host
            // and port it failed to reach.
            AuraResult.Failed(AuraError.Offline)

        } catch (e: SerializationException) {
            // The converter threw: the server answered, and this build cannot
            // read what it said. Caught *before* the generic clause because a
            // SerializationException is a RuntimeException, so it used to land
            // in `Unknown` - "something went wrong reaching Aura" - about a
            // server that had been reached perfectly. Its message is dropped
            // rather than passed on: it quotes the JSON it choked on.
            AuraResult.Failed(AuraError.Incompatible("unreadable body"))

        } catch (e: Exception) {
            AuraResult.Failed(AuraError.Unknown())
        }
    }

    /**
     * Tell "Aura says no" apart from "Aura is not up yet".
     *
     * Both arrive as 503. Aura itself only ever sends one - the screen
     * endpoints, when observation is switched off server-side - and it
     * names itself in the body. A free-tier platform sends the other from
     * its own edge while the container starts, with no such marker.
     *
     * The distinction is worth a body read because the two lead the user
     * opposite ways: one means "turn it on in the server config", the
     * other means "wait ten seconds". The body is matched against and then
     * dropped; it is never shown, so nothing it contains reaches the UI.
     */
    private fun unavailableReason(response: Response<*>): AuraError {

        val body = runCatching { response.errorBody()?.string() }
            .getOrNull()
            .orEmpty()

        return if (SCREEN_DISABLED in body) {
            AuraError.Unavailable(
                "Screen observation is switched off on the Aura server."
            )
        } else {
            AuraError.Waking
        }
    }

    /**
     * Pull the server's own explanation out of a 422.
     *
     * Two different shapes arrive with that code and only one of them is
     * worth showing. Aura's settings routes raise
     * `HTTPException(422, detail={"error": ..., "message": ...})`, and that
     * message is written for a person holding a phone. FastAPI's *own*
     * request validation also returns 422, but as a list of pydantic error
     * objects full of `loc`/`type`/`ctx` internals - useless on a phone and
     * the kind of thing that leaks implementation detail into the UI.
     *
     * So: an object with a `message` string is shown verbatim; anything
     * else becomes a generic refusal. Parsing is wrapped because an error
     * body is not guaranteed to be JSON at all.
     */
    private fun rejection(response: Response<*>): AuraError {

        val message = runCatching {

            val body = response.errorBody()?.string().orEmpty()

            val detail = ApiFactory.json
                .parseToJsonElement(body)
                .jsonObject["detail"]

            (detail as? JsonObject)
                ?.get("message")
                ?.jsonPrimitive
                ?.contentOrNull
                .orEmpty()

        }.getOrNull().orEmpty()

        return AuraError.Rejected(message)
    }

    /**
     * Turn dotted paths into the nested object the server merges.
     *
     * `{"llm.provider": "groq"}` becomes `{"llm": {"provider": "groq"}}`.
     * The server's `flatten()` would accept the dotted form directly, but
     * sending the nested shape keeps the request identical to what
     * `tests/test_settings_api.py` pins, so the contract the tests prove is
     * the contract the phone uses.
     */
    private fun nest(changes: Map<String, JsonElement>): JsonObject {

        val tree = mutableMapOf<String, Any>()

        for ((path, value) in changes) {

            val parts = path.split(".")

            var level = tree

            for (part in parts.dropLast(1)) {
                @Suppress("UNCHECKED_CAST")
                level = level.getOrPut(part) { mutableMapOf<String, Any>() }
                    as? MutableMap<String, Any>
                    // A caller asked for both "a.b" and "a.b.c". One has to
                    // lose; dropping the deeper write silently would be
                    // worse than the shallower one winning visibly.
                    ?: return JsonObject(emptyMap())
            }

            level[parts.last()] = value
        }

        return buildTree(tree)
    }

    @Suppress("UNCHECKED_CAST")
    private fun buildTree(level: Map<String, Any>): JsonObject =
        JsonObject(
            level.mapValues { (_, value) ->
                when (value) {
                    is JsonElement -> value
                    else -> buildTree(value as Map<String, Any>)
                }
            }
        )

    private companion object {
        const val SCREEN_DISABLED = "screen_disabled"
    }
}

data class ChatReply(
    val sessionId: String,
    val reply: String,
    val messageId: String,
)
