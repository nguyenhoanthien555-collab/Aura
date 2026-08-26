package com.aura.companion.data.remote

import com.aura.companion.data.AuraError
import com.aura.companion.data.settings.SettingsProvider
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * Streaming chat, over the WebSocket the server already exposes.
 *
 * Retrofit does not model WebSockets, so this sits beside [AuraApi] rather
 * than inside it. The protocol is the one in `server/routes/ws_chat.py`,
 * and nothing here invents a second one: connect, send one message frame,
 * read `started` then `chunk`* then `complete`.
 *
 * Authentication travels as `?token=`, because a WebSocket handshake has
 * no place to put an Authorization header. That is the server's design and
 * this follows it; the token is never logged, and the URL carrying it is
 * never logged either.
 *
 * One socket per message, closed when the reply finishes. A persistent
 * socket would cost a wakelock-adjacent background connection for a
 * conversation that is idle almost all of the time, and the server treats
 * each connection as one turn regardless.
 */
class AuraStreamClient(
    private val settings: SettingsProvider,
) {

    /**
     * Send `message` and emit the reply as it arrives.
     *
     * The flow always terminates: with [StreamEvent.Complete] on success or
     * [StreamEvent.Failed] on any error. It never throws, for the same
     * reason [com.aura.companion.data.AuraRepository] never throws - a
     * network exception's message can name a host or a path.
     *
     * Cancelling the collector closes the socket.
     */
    fun stream(
        message: String,
        sessionId: String?,
        context: JsonObject = JsonObject(emptyMap()),
    ): Flow<StreamEvent> = callbackFlow {

        val url = streamUrl(sessionId)

        if (url == null) {
            trySend(StreamEvent.Failed(AuraError.NotConfigured))
            close()
            return@callbackFlow
        }

        val webSocketRef = AtomicReference<WebSocket?>(null)

        val socket = client().newWebSocket(
            Request.Builder().url(url).build(),
            object : WebSocketListener() {

                override fun onOpen(webSocket: WebSocket, response: Response) {
                    webSocketRef.set(webSocket)
                    // The server reads exactly one frame, then replies.
                    // `context` rides along when the caller has one - the
                    // frame schema already carried the field, and the
                    // route forwards it into the same conversation
                    // pipeline a REST turn uses.
                    webSocket.send(
                        ApiFactory.json.encodeToString(
                            StreamRequestDto.serializer(),
                            StreamRequestDto(message = message, context = context),
                        )
                    )
                }

                override fun onMessage(webSocket: WebSocket, text: String) {

                    val event = parse(text)

                    trySend(event)

                    // `complete` and `error` are both terminal in the
                    // server's protocol. Initiate the close handshake but do
                    // not close the flow yet - wait for onClosed so the
                    // WebSocket is fully torn down before the callbackFlow
                    // terminates. This avoids "queue not shut down" errors in
                    // MockWebServer during test teardown.
                    if (event is StreamEvent.Complete || event is StreamEvent.Failed) {
                        webSocket.close(NORMAL_CLOSURE, null)
                    }
                }

                override fun onFailure(
                    webSocket: WebSocket,
                    t: Throwable,
                    response: Response?,
                ) {
                    trySend(StreamEvent.Failed(errorFor(t, response)))
                    close()
                }

                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    // Server initiated close - respond but wait for onClosed
                    webSocket.close(NORMAL_CLOSURE, null)
                }

                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                    // WebSocket fully closed - now safe to terminate the flow
                    close()
                }
            },
        )

        awaitClose {
            webSocketRef.get()?.close(NORMAL_CLOSURE, null)
            socket.cancel()
        }
    }

    // ------------------------------------------------------------------
    // Wiring
    // ------------------------------------------------------------------

    /**
     * The client used for streaming.
     *
     * `readTimeout(0)` because a WebSocket is idle between chunks by
     * definition, and the read timeout that protects a REST call would
     * kill a slow generation mid-sentence. A ping interval replaces it:
     * OkHttp fails the socket when a ping goes unanswered, which detects a
     * genuinely dead connection without penalising a slow one.
     */
    private fun client() =
        ApiFactory.client(settings).newBuilder()
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .pingInterval(PING_SECONDS, TimeUnit.SECONDS)
            .build()

    /**
     * `https://host/` becomes `https://host/api/chat/stream?...`.
     *
     * Built as an http(s) URL on purpose: OkHttp performs the upgrade
     * itself and accepts the ordinary scheme, so the base URL the user
     * typed for REST works for streaming with no second setting to get
     * wrong.
     */
    private fun streamUrl(sessionId: String?) =
        settings.current.serverUrl
            .takeIf { it.isNotBlank() }
            ?.toHttpUrlOrNull()
            ?.newBuilder()
            ?.addPathSegments(STREAM_PATH)
            ?.apply {
                settings.current.authToken
                    .takeIf { it.isNotBlank() }
                    ?.let { addQueryParameter("token", it) }

                sessionId
                    ?.takeIf { it.isNotBlank() }
                    ?.let { addQueryParameter("session_id", it) }
            }
            ?.build()

    private fun parse(text: String): StreamEvent {

        val frame = runCatching {
            ApiFactory.json.parseToJsonElement(text) as? JsonObject
        }.getOrNull() ?: return StreamEvent.Failed(AuraError.Unknown())

        fun str(key: String) =
            (frame[key] as? JsonPrimitive)?.takeIf { it.isString }?.content

        fun num(key: String) = (frame[key] as? JsonPrimitive)?.doubleOrNull

        return when (str("type")) {

            "started" -> StreamEvent.Started(
                sessionId = str("session_id").orEmpty(),
                messageId = str("message_id").orEmpty(),
            )

            "chunk" -> StreamEvent.Chunk(
                text = str("chunk").orEmpty(),
                index = (frame["index"] as? JsonPrimitive)?.intOrNull ?: 0,
            )

            "complete" -> StreamEvent.Complete(
                sessionId = str("session_id").orEmpty(),
                messageId = str("message_id").orEmpty(),
                totalChunks = (frame["total_chunks"] as? JsonPrimitive)?.intOrNull ?: 0,
                elapsedSeconds = num("elapsed_seconds") ?: 0.0,
                firstChunkSeconds = num("first_chunk_seconds"),
            )

            "reaction" -> StreamEvent.Reaction(
                sessionId = str("session_id").orEmpty(),
                messageId = str("message_id").orEmpty(),
                emoji = str("emoji").orEmpty()
            )

            // The server names the reason: invalid_json, empty_message,
            // message_too_long, stream_failed, internal_error. They are
            // mapped rather than shown, because they are protocol
            // vocabulary and not something to put in front of a person.
            "error" -> StreamEvent.Failed(
                when (str("error")) {
                    "message_too_long" -> AuraError.ServerFailure(413)
                    "empty_message", "invalid_json" -> AuraError.ServerFailure(422)
                    else -> AuraError.ServerFailure(500)
                }
            )

            else -> StreamEvent.Failed(AuraError.Unknown())
        }
    }

    /**
     * Why the socket died, in the same vocabulary the REST path uses.
     *
     * A handshake the server refused arrives here as a `Response` with an
     * ordinary HTTP status, because the upgrade never happened - which is
     * how a rejected `?token=` is told apart from a dead network.
     */
    private fun errorFor(t: Throwable, response: Response?): AuraError = when {

        response?.code == 401 -> AuraError.Unauthorized

        // Recognised and refused. Split from 401 for the same reason the REST
        // path splits it: one says replace the token, the other says the token
        // is fine and this request is not allowed.
        response?.code == 403 -> AuraError.Forbidden

        response?.code == 429 -> AuraError.RateLimited

        // The same reasoning as the REST path: a gateway error from a free
        // tier is a container starting, not a fault to report.
        response?.code == 502 || response?.code == 503 || response?.code == 504 ->
            AuraError.Waking

        response != null -> AuraError.ServerFailure(response.code)

        t is SocketTimeoutException -> AuraError.Timeout

        t is UnknownHostException -> AuraError.Offline

        // Includes the socket dying on a Wi-Fi to mobile handover.
        else -> AuraError.Offline
    }

    private companion object {
        const val STREAM_PATH = "api/chat/stream"
        const val NORMAL_CLOSURE = 1000
        const val PING_SECONDS = 20L
    }
}

/**
 * One turn of a streamed reply.
 *
 * Mirrors the `type` field of the server's frames, minus the transport
 * details a caller cannot act on.
 */
sealed interface StreamEvent {

    /** The server accepted the message and is generating. */
    data class Started(
        val sessionId: String,
        val messageId: String,
    ) : StreamEvent

    /** A fragment of the reply, in order. */
    data class Chunk(
        val text: String,
        val index: Int,
    ) : StreamEvent

    data class Reaction(
        val sessionId: String,
        val messageId: String,
        val emoji: String,
    ) : StreamEvent

    /**
     * The reply finished.
     *
     * The timings are the server's own measurements, kept because they are
     * the only view this app has of where a slow reply spent its time.
     */
    data class Complete(
        val sessionId: String,
        val messageId: String,
        val totalChunks: Int,
        val elapsedSeconds: Double,
        val firstChunkSeconds: Double?,
    ) : StreamEvent

    /** Terminal failure. Nothing further will arrive. */
    data class Failed(val error: AuraError) : StreamEvent
}
