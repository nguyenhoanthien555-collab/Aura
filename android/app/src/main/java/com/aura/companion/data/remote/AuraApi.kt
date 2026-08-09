package com.aura.companion.data.remote

import okhttp3.MultipartBody
import okhttp3.RequestBody
import retrofit2.Response
import retrofit2.http.Body
import retrofit2.http.GET
import retrofit2.http.Multipart
import retrofit2.http.POST
import retrofit2.http.Part
import retrofit2.http.Query

/**
 * The Aura server, as this app sees it.
 *
 * One interface, one place to look when the API changes. No Composable
 * and no ViewModel ever constructs a request - they call the repository,
 * which calls this.
 *
 * Every method returns `Response<T>` rather than the bare body so the
 * repository can distinguish 401 from 503 from a parse failure. The
 * difference matters: a 401 means "re-enter your token", a 503 means
 * "the server is waking up, wait", and telling the user the wrong one is
 * how a working setup gets reconfigured for no reason.
 */
interface AuraApi {

    @GET("api/health")
    suspend fun health(): Response<HealthDto>

    @POST("api/chat")
    suspend fun chat(@Body request: ChatRequestDto): Response<ChatResponseDto>

    // Streaming chat is deliberately absent from this interface. The server
    // mounts it as a WebSocket at `api/chat/stream` (server/routes/ws_chat.py)
    // and authenticates it with a `?token=` query parameter, because a
    // handshake cannot carry an Authorization header. Retrofit does not model
    // WebSockets, so it is opened directly through OkHttp in AuraStreamClient.

    @POST("api/screen")
    suspend fun screen(@Body request: ScreenRequestDto): Response<ScreenResponseDto>

    @Multipart
    @POST("api/screen/upload")
    suspend fun uploadScreenshot(
        @Part("session_id") sessionId: RequestBody,
        @Part("device_id") deviceId: RequestBody,
        @Part("application") application: RequestBody,
        @Part("package") packageName: RequestBody,
        @Part("timestamp") timestamp: RequestBody,
        @Part screenshot: MultipartBody.Part,
    ): Response<UploadResponseDto>

    @GET("api/notifications")
    suspend fun notifications(
        @Query("device_id") deviceId: String,
    ): Response<NotificationsResponseDto>
}
