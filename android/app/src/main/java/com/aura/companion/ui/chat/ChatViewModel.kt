package com.aura.companion.ui.chat

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.aura.companion.accessibility.AuraAccessibilityService
import com.aura.companion.accessibility.IntentRouter
import com.aura.companion.data.AuraError
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.AuraResult
import com.aura.companion.data.chat.Transcript
import com.aura.companion.data.local.DeviceTelemetryProbe
import com.aura.companion.data.remote.ChatGPTWebClient
import com.aura.companion.data.remote.StreamEvent
import com.aura.companion.data.settings.SettingsProvider
import com.aura.companion.voice.AuraVoiceManager
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.util.UUID

/**
 * The chat screen's brain.
 *
 * Holds the conversation, drives the connection banner, and turns an
 * [AuraError] into something a person can act on. It never touches HTTP -
 * that is the repository's job - and it never formats a Composable.
 *
 * The one subtlety worth stating: a message the user typed is added to the
 * list *before* the request goes out, and marked `failed` if the request
 * fails. Dropping it on failure loses what they wrote, which on a mobile
 * link with intermittent signal is the difference between an app you trust
 * with a long message and one you don't.
 */
// Compiled once, not per streamed token (see streamReply's Chunk handler).
private val REACT_REGEX = "\\[REACT:\\s*([^\\]]+)\\]".toRegex()

class ChatViewModel(
    private val repository: AuraRepository,
    private val settings: SettingsProvider,
    private val transcript: Transcript = Transcript.None,
    private val isOnline: () -> Boolean = { true },
    private val context: Context? = null,
    private val voiceManager: AuraVoiceManager? = null,
) : ViewModel() {

    private val _state = MutableStateFlow(
        ChatUiState(
            messages = restored(),
            isConfigured = settings.current.isConfigured,
            connection = ConnectionState.Unknown,
            isInitialScanning = settings.current.isConfigured,
            scanStatusText = if (settings.current.isConfigured) "Aura đang kết nối & nhận diện hệ thống..." else "",
        )
    )
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    private var probe: Job? = null
    private var currentSendJob: Job? = null

    fun toggleThinkingMode() {
        _state.update { it.copy(isThinkingEnabled = !it.isThinkingEnabled) }
    }

    fun cancelCurrentTurn() {
        currentSendJob?.cancel()
        currentSendJob = null
        AuraAccessibilityService.stopAgentTask()
        voiceManager?.stopSpeaking()
        voiceManager?.stopListening()
        ChatGPTWebClient.cancelCurrentCall()
        _state.update { current ->
            current.copy(
                messages = current.messages.map {
                    if (it.streaming) it.copy(streaming = false) else it
                },
                isSending = false,
                isAgentRunning = false,
                isHandsFreeMode = false,
                agentStatusText = "",
            )
        }
    }

    /**
     * What the store already holds, so a launch does not rewrite it.
     *
     * Seeded with the restored conversation. Without this the collector's
     * first emission is whatever was just read, and every launch would
     * serialise and re-encrypt two hundred messages to store exactly what was
     * already there. `distinctUntilChanged` cannot see that, because the
     * first value it sees is by definition not a repeat.
     */
    private var kept: List<ChatMessage> = _state.value.messages

    init {
        viewModelScope.launch {
            settings.settings.collect { current ->
                _state.update { it.copy(isConfigured = current.isConfigured) }
            }
        }
        keep()
        observeTranscript()
        checkConnection()

        if (voiceManager != null) {
            voiceManager.onSpeechDoneListener = {
                if (_state.value.isHandsFreeMode && !_state.value.isSending && !_state.value.isSpeaking) {
                    startHandsFreeListening()
                }
            }
            viewModelScope.launch {
                voiceManager.isSpeaking.collect { speaking ->
                    _state.update { it.copy(isSpeaking = speaking) }
                }
            }
            viewModelScope.launch {
                voiceManager.isListening.collect { listening ->
                    _state.update { it.copy(isListening = listening) }
                }
            }
            viewModelScope.launch {
                voiceManager.rmsDb
                    .map { ((it * 2f).toInt()) / 2f }
                    .distinctUntilChanged()
                    .collect { rms ->
                        _state.update { it.copy(speechRmsDb = rms) }
                    }
            }
        }

        viewModelScope.launch {
            while (isActive) {
                delay(20_000L)
                if (settings.current.isConfigured && isOnline() && _state.value.connection is ConnectionState.Connected) {
                    refreshTelemetry()
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // History (§15)
    // ------------------------------------------------------------------

    /**
     * The conversation the last run left behind.
     *
     * In the initial state rather than loaded from `init`, so the first frame
     * the screen ever draws already has it. Loading a moment later would show
     * an empty conversation that filled in - which reads as a lost transcript
     * for exactly as long as anyone notices.
     *
     * A read that fails is an empty conversation and nothing more. An
     * exception here would be an app that will not open, and what the person
     * holding the phone can do about a Keystore that has become unavailable
     * is nothing.
     */
    private fun restored(): List<ChatMessage> = try {
        transcript.read().messages.map { it.rendered() }
    } catch (error: Exception) {
        emptyList()
    }

    /**
     * Keep the store in step with the screen.
     *
     * One collector rather than a save at each of the five places a message
     * is added, because the five would become six and the sixth would be the
     * one that forgot.
     *
     * TWO THINGS IT WILL NOT WRITE
     * ----------------------------
     * *A reply that is still arriving.* Excluding it from the projection is
     * not only about the flag - it is what stops a save per token. A real
     * store rewrites its whole file on commit, so mirroring the screen
     * literally would mean a file write for every few characters Aura says.
     * With the growing bubble excluded the projection is constant for the
     * whole of a streamed reply, and the write happens once, when it settles.
     *
     * *Emptiness.* Every route to an empty screen other than
     * [newConversation] is a failure of some kind - most sharply a read that
     * threw, which leaves the screen empty while the file it could not read
     * is still there. Writing that back would destroy the transcript rather
     * than fail to load it, and §41 is explicit that existing data is not
     * ours to destroy. So emptiness is only ever stored on purpose, by
     * [newConversation] calling `clear`.
     */
    private fun keep() {
        viewModelScope.launch {
            state
                .map { current -> current.messages.filterNot { it.streaming } }
                .distinctUntilChanged()
                .collect { messages ->

                    if (messages.isEmpty() || messages == kept) return@collect

                    try {
                        transcript.write(messages.map { it.stored() })
                        kept = messages
                    } catch (error: Exception) {
                        // The conversation on screen is unaffected, and `kept`
                        // is left alone so the next change tries again. Losing
                        // the transcript at the next launch is bad; losing this
                        // turn to a failed write would be worse.
                    }
                }
        }
    }

    /**
     * Adopt changes another owner of this transcript made.
     *
     * The floating overlay runs its own [ChatViewModel] over the same
     * [com.aura.companion.data.chat.TranscriptStore] singleton. Each used to
     * read once at construction and never again, so a message sent in one
     * surface stayed invisible in the other until the app was relaunched.
     * Collecting the store's stream keeps them in step live.
     *
     * `drop(1)` skips the stream's initial value: it is the conversation this
     * ViewModel already restored into its first frame, and reacting to it
     * would either be a no-op or, when the last read failed, wrongly resurrect
     * a transcript we deliberately treated as empty. Only later emissions are
     * real external changes.
     *
     * Compared and adopted in the *persisted* shape ([stored]) because that is
     * the only shape both surfaces agree on: reactions and the `streaming`
     * flag are not stored, so a difference in only those is not an external
     * change and must not trigger adoption. A reply still arriving here is
     * kept on top of whatever the store now holds, and this surface's own
     * reactions are preserved by id. Setting [kept] to the adopted list stops
     * [keep] from writing it straight back.
     */
    private fun observeTranscript() {
        viewModelScope.launch {
            transcript.changes.drop(1).collect { stored ->

                val settled = _state.value.messages.filterNot { it.streaming }
                if (stored.messages == settled.map { it.stored() }) return@collect

                val existingById = _state.value.messages.associateBy { it.id }
                val adopted = stored.messages.map { sm ->
                    sm.rendered().copy(
                        reactions = existingById[sm.id]?.reactions ?: emptyMap()
                    )
                }
                val streaming = _state.value.messages.filter { it.streaming }

                kept = adopted
                _state.update { it.copy(messages = adopted + streaming) }
            }
        }
    }

    // ------------------------------------------------------------------
    // Input
    // ------------------------------------------------------------------

    fun onDraftChanged(text: String) {
        _state.update { it.copy(draft = text) }
    }

    fun dismissError() {
        _state.update { it.copy(error = null) }
    }

    /**
     * Start a fresh conversation.
     *
     * Clears the session id, so the server allocates a new one and does not
     * carry the old context forward, and clears the stored transcript.
     *
     * Clearing the messages was once cosmetic - the session id was the only
     * thing that survived the tap. Since §15 it is the opposite: leaving the
     * store alone would bring the old conversation back at the next launch,
     * sitting under a session id the server has never heard of. This is also
     * the only place emptiness is ever written, which is why `keep` refuses
     * to write it and this says so explicitly.
     */
    fun newConversation() {

        repository.resetSession()

        try {
            transcript.clear()
            kept = emptyList()
        } catch (error: Exception) {
            // Nothing to do but carry on with a fresh screen.
        }

        _state.update { it.copy(messages = emptyList(), error = null) }
    }

    // ------------------------------------------------------------------
    // Connection
    // ------------------------------------------------------------------

    /**
     * Probe the server and update the banner.
     *
     * The "waking up" state is time-based rather than error-based: a
     * suspended free-tier service accepts the TCP connection immediately
     * and then holds the request open while the container starts, so
     * nothing fails - it just takes a long time. After four seconds
     * without an answer we say so, because a silent spinner reads as a
     * broken app.
     */
    fun checkConnection() {

        if (!settings.current.isConfigured) {
            _state.update {
                it.copy(
                    connection = ConnectionState.Unavailable("Not configured"),
                    isInitialScanning = false,
                    scanStatusText = "",
                )
            }
            return
        }

        if (!isOnline()) {
            _state.update {
                it.copy(
                    connection = ConnectionState.Unavailable("Offline"),
                    isInitialScanning = false,
                    scanStatusText = "",
                )
            }
            return
        }

        probe?.cancel()

        probe = viewModelScope.launch {

            val isFirstScan = _state.value.isInitialScanning || _state.value.connection == ConnectionState.Unknown
            _state.update {
                it.copy(
                    connection = ConnectionState.Connecting,
                    isInitialScanning = isFirstScan,
                    scanStatusText = if (isFirstScan) "Aura đang kết nối & nhận diện hệ thống..." else it.scanStatusText,
                )
            }

            val slowNotice = launch {
                delay(WAKE_NOTICE_MS)
                if (isActive) {
                    _state.update { it.copy(connection = ConnectionState.WakingUp) }
                }
            }

            val start = System.currentTimeMillis()
            when (val telemResult = repository.telemetry()) {
                is AuraResult.Ok -> {
                    slowNotice.cancel()
                    val ping = (System.currentTimeMillis() - start).coerceAtLeast(1L)
                    val phone = context?.let { DeviceTelemetryProbe.sample(it, ping) }
                    val auraDto = telemResult.value.aura
                    val hostDto = telemResult.value.host
                    val providerName = auraDto.llmProvider.ifBlank { "aura" }
                    _state.update {
                        it.copy(
                            connection = ConnectionState.Connected(providerName),
                            hostTelemetry = hostDto,
                            phoneTelemetry = phone,
                            pingMs = ping,
                            isInitialScanning = false,
                            scanStatusText = "",
                            error = null,
                        )
                    }
                }
                is AuraResult.Failed -> {
                    when (val result = repository.health()) {
                        is AuraResult.Ok -> {
                            slowNotice.cancel()
                            val ping = (System.currentTimeMillis() - start).coerceAtLeast(1L)
                            val phone = context?.let { DeviceTelemetryProbe.sample(it, ping) }
                            _state.update {
                                it.copy(
                                    connection = ConnectionState.Connected(
                                        result.value.runtime["llm_provider"] ?: "aura"
                                    ),
                                    phoneTelemetry = phone,
                                    pingMs = ping,
                                    isInitialScanning = false,
                                    scanStatusText = "",
                                    error = null,
                                )
                            }
                        }
                        is AuraResult.Failed -> {
                            slowNotice.cancel()
                            _state.update {
                                it.copy(
                                    connection = ConnectionState.Unavailable(
                                        result.error.userMessage
                                    ),
                                    isInitialScanning = false,
                                    scanStatusText = "",
                                    error = if (
                                        result.error is AuraError.Unauthorized ||
                                        result.error is AuraError.Forbidden
                                    ) {
                                        result.error
                                    } else {
                                        it.error
                                    },
                                )
                            }
                        }
                    }
                }
            }
        }
    }

    /**
     * Poll telemetry in real-time to update Host PC and Phone hardware metrics.
     */
    fun refreshTelemetry() {
        viewModelScope.launch {
            val start = System.currentTimeMillis()
            when (val telem = repository.telemetry()) {
                is AuraResult.Ok -> {
                    val ping = (System.currentTimeMillis() - start).coerceAtLeast(1L)
                    val phone = context?.let { DeviceTelemetryProbe.sample(it, ping) }
                    _state.update {
                        it.copy(
                            hostTelemetry = telem.value.host,
                            phoneTelemetry = phone,
                            pingMs = ping,
                        )
                    }
                }
                is AuraResult.Failed -> {
                    context?.let { ctx ->
                        val phone = DeviceTelemetryProbe.sample(ctx, _state.value.pingMs)
                        _state.update { it.copy(phoneTelemetry = phone) }
                    }
                }
            }
        }
    }

    // ------------------------------------------------------------------
    // Sending
    // ------------------------------------------------------------------

    fun react(messageId: String, emoji: String) {
        // Optimistically update the UI
        _state.update { current ->
            val updatedMessages = current.messages.map { msg ->
                if (msg.id == messageId) {
                    val newReactions = msg.reactions.toMutableMap()
                    newReactions["user"] = emoji // For now, simple user reaction override
                    msg.copy(reactions = newReactions)
                } else {
                    msg
                }
            }
            current.copy(messages = updatedMessages)
        }
        
        // TODO: Call backend to persist reaction
    }

    fun attachImage(bitmap: androidx.compose.ui.graphics.ImageBitmap, base64: String) {
        _state.update {
            it.copy(
                attachedImageBitmap = bitmap,
                attachedImageBase64 = base64,
            )
        }
    }

    fun clearAttachment() {
        _state.update {
            it.copy(
                attachedImageBitmap = null,
                attachedImageBase64 = null,
            )
        }
    }

    fun send() {

        val text = _state.value.draft.trim()
        val attachedImageB64 = _state.value.attachedImageBase64
        val attachedImageBmp = _state.value.attachedImageBitmap

        if ((text.isEmpty() && attachedImageB64 == null) || _state.value.isSending) return

        if (!settings.current.isConfigured) {
            _state.update { it.copy(error = AuraError.NotConfigured) }
            return
        }

        if (!isOnline()) {
            _state.update {
                it.copy(
                    error = AuraError.Offline,
                    connection = ConnectionState.Unavailable("Offline"),
                )
            }
            return
        }

        val messageText = if (text.isNotBlank()) text else "Phân tích và cho tôi biết về hình ảnh này."

        val outgoing = ChatMessage(
            id = UUID.randomUUID().toString(),
            text = messageText,
            author = ChatMessage.Author.USER,
            imageBitmap = attachedImageBmp,
        )

        _state.update {
            it.copy(
                messages = it.messages + outgoing,
                draft = "",
                attachedImageBase64 = null,
                attachedImageBitmap = null,
                isSending = true,
                error = null,
            )
        }

        currentSendJob?.cancel()
        currentSendJob = viewModelScope.launch {

            // Only a message that actually asks for something to happen
            // on the phone goes into the agent loop. Deciding this needs
            // a server round-trip, so it happens here rather than in
            // `send` itself, which is not suspending.
            if (attachedImageB64 == null && wantsDeviceAction(messageText) && startAgentTask(messageText)) {
                return@launch
            }

            val slowNotice = launch {
                delay(WAKE_NOTICE_MS)
                if (isActive) {
                    _state.update { it.copy(connection = ConnectionState.WakingUp) }
                }
            }

            // The phone knows which app is in front of the owner; the
            // server does not unless this message says so. Built once per
            // turn and used by whichever transport answers.
            val context = conversationContext(attachedImageB64)

            val streamed = streamReply(messageText, slowNotice, outgoing.id, context)

            // Falling back rather than reporting a failure: a proxy that
            // will not carry a WebSocket is a deployment property, not
            // something the person typing can fix. REST answers the same
            // question with the same session, so the conversation
            // continues and the only visible difference is that the reply
            // arrives whole instead of growing.
            if (!streamed) {
                sendOverRest(outgoing.id, messageText, slowNotice, context)
            }
        }
    }

    /**
     * What this phone can tell the server about the screen, per message.
     *
     * Two halves, both honest:
     *
     *  - `app`: the foreground package/label/activity from the
     *    accessibility layer. This is metadata, not observation - it rides
     *    with a message the owner deliberately sent, and answers "app gì
     *    vậy?" without any pixels. Screen *text* still travels only
     *    through [com.aura.companion.screen.ScreenObservationService]
     *    behind its own switch; nothing here becomes a second, ungated
     *    copy of that stream.
     *
     *  - `screen_note`: one sentence, written on the phone, about what
     *    this phone's screen pipeline cannot do right now - quoted by the
     *    server verbatim rather than re-derived. When pixels flow, there
     *    is no note: absence of bad news means there is no bad news.
     *
     * Empty when the accessibility service is not connected, so an older
     * or un-permissioned install sends exactly what it always sent.
     */
    private fun conversationContext(attachedImageB64: String? = null): JsonObject {

        val entries = mutableMapOf<String, JsonElement>()

        val app = AuraAccessibilityService.currentForegroundApp()
        if (app != null) {
            entries["app"] = JsonObject(
                buildMap {
                    put("package", JsonPrimitive(app.packageName))
                    if (app.label.isNotBlank()) put("label", JsonPrimitive(app.label))
                    app.activity?.takeIf { it.isNotBlank() }?.let {
                        put("activity", JsonPrimitive(it))
                    }
                }
            )
        }

        screenNote()?.let { entries["screen_note"] = JsonPrimitive(it) }

        if (attachedImageB64 != null) {
            entries["image"] = JsonPrimitive(attachedImageB64)
            entries["image_mime"] = JsonPrimitive("image/jpeg")
        }

        entries["is_thinking_enabled"] = JsonPrimitive(_state.value.isThinkingEnabled)

        return JsonObject(entries)
    }

    /**
     * Why "look at my screen" cannot mean pixels right now, or nothing.
     *
     * Mirrors the gates [com.aura.companion.screen.ScreenshotUploader]
     * applies, in the same order, so the sentence the server quotes is
     * the reason the uploader would have given.
     */
    private fun screenNote(): String? = when {
        !settings.current.screenObservationEnabled ->
            "Screen observation is switched off on this phone, so Aura " +
                "cannot read what is on screen beyond the foreground app."
        !settings.current.uploadScreenshots ->
            "Screenshot upload is switched off on this phone, so Aura " +
                "cannot see the screen's pixels."
        else -> null
    }

    /**
     * Should this message drive the phone rather than be answered?
     *
     * False whenever there is no accessibility service to drive it, and
     * false whenever the routing question itself could not be answered.
     * A probe that fails means the server is unreachable, and the normal
     * path reports that honestly - starting a device loop against a
     * server the loop is about to need would fail later and less
     * clearly.
     */
    private suspend fun wantsDeviceAction(text: String): Boolean {

        if (!AuraAccessibilityService.isEnabled()) return false

        return when (val result = repository.send(text, IntentRouter.PROBE_CONTEXT)) {
            is AuraResult.Ok -> IntentRouter.isAction(result.value.reply)
            is AuraResult.Failed -> false
        }
    }

    /**
     * Hand the request to the accessibility agent.
     *
     * False if the service went away between the check and here - it can
     * be switched off in Settings mid-turn - in which case the caller
     * falls through to the conversational path and the user gets an
     * answer instead of silence.
     */
    private fun startAgentTask(text: String): Boolean =

        AuraAccessibilityService.startAgentTask(text) { finalReply ->
            _state.update { current ->
                current.copy(
                    messages = current.messages + ChatMessage(
                        id = UUID.randomUUID().toString(),
                        text = finalReply,
                        author = ChatMessage.Author.AURA,
                    ),
                    isSending = false,
                )
            }
        }

    /**
     * Stream the reply, returning false if the socket never delivered one.
     *
     * False means "nothing usable arrived" - the socket failed before any
     * text did. Once a chunk has been rendered the turn belongs to
     * streaming, so a failure after that is reported rather than retried:
     * re-sending would ask the model the same question twice and show the
     * user two answers.
     */
    private suspend fun streamReply(
        message: String,
        slowNotice: Job,
        outgoingId: String,
        context: JsonObject = JsonObject(emptyMap()),
    ): Boolean {

        val messageId = UUID.randomUUID().toString()
        var reply = ""
        var reacted = false
        var failure: AuraError? = null

        repository.stream(message, context).collect { event ->

            when (event) {

                is StreamEvent.Started -> {
                    slowNotice.cancel()
                }

                is StreamEvent.Reaction -> {
                    _state.update { current ->
                        current.copy(
                            messages = current.messages.map {
                                if (it.id == outgoingId) {
                                    val newReactions = it.reactions.toMutableMap()
                                    newReactions["aura"] = event.emoji
                                    it.copy(reactions = newReactions)
                                } else it
                            }
                        )
                    }
                }

                is StreamEvent.Chunk -> {

                    val first = reply.isEmpty()

                    reply += event.text

                    // Parse fallback REACT tag for models that don't tool-call well.
                    // Cheap substring guard first so we don't run the (hoisted) regex
                    // over the whole accumulated reply on every single streamed token.
                    val match = if (!reacted && reply.contains("[REACT")) REACT_REGEX.find(reply) else null
                    if (match != null) {
                        val emoji = match.groupValues[1].trim()
                        reply = reply.replace(match.value, "").trim()
                        reacted = true
                        
                        _state.update { current ->
                            current.copy(
                                messages = current.messages.map {
                                    if (it.id == outgoingId) {
                                        val newReactions = it.reactions.toMutableMap()
                                        newReactions["aura"] = emoji
                                        it.copy(reactions = newReactions)
                                    } else it
                                }
                            )
                        }
                    }

                    if (reply.isNotEmpty()) {
                        _state.update { current ->
                            current.copy(
                                messages = if (first && !current.messages.any { it.id == messageId }) {
                                    current.messages + ChatMessage(
                                        id = messageId,
                                        author = ChatMessage.Author.AURA,
                                        text = reply,
                                        streaming = true,
                                    )
                                } else {
                                    current.messages.map {
                                        if (it.id == messageId) it.copy(text = reply) else it
                                    }
                                },
                                connection = ConnectionState.Connected(
                                    (current.connection as? ConnectionState.Connected)?.provider ?: "aura"
                                )
                            )
                        }
                    } else if (reacted) {
                        // If all we got was a reaction, clear out the empty bubble if it was created
                        _state.update { current ->
                            current.copy(messages = current.messages.filter { it.id != messageId })
                        }
                    }
                }

                is StreamEvent.ToolConsentRequest -> {
                    startToolConsentCountdown(event.requestId, event.toolName, event.toolDescription)
                }

                is StreamEvent.Complete -> {
                    val isVerified = event.verifier?.let { v ->
                        val dec = (v["decision"] as? JsonPrimitive)?.content
                        dec == "pass" || dec == "repair"
                    } ?: false

                    val finalText = event.text?.takeIf { t -> t.isNotBlank() } ?: reply
                    if (_state.value.isTtsEnabled && finalText.isNotBlank()) {
                        voiceManager?.speak(finalText)
                    }

                    val reportedProvider = event.provider?.takeIf { it.isNotBlank() }
                    _state.update { current ->
                        current.copy(
                            messages = current.messages.map {
                                if (it.id == messageId) {
                                    it.copy(
                                        text = finalText,
                                        streaming = false,
                                        verified = if (isVerified) true else null,
                                    )
                                } else it
                            },
                            connection = if (reportedProvider != null) {
                                ConnectionState.Connected(reportedProvider)
                            } else current.connection,
                            isSending = false,
                            pendingToolConsent = null,
                        )
                    }
                }

                is StreamEvent.Failed -> {
                    failure = event.error
                }
            }
        }

        slowNotice.cancel()

        // Nothing arrived and no reaction was processed: let the caller try the REST path.
        if (reply.isEmpty() && !reacted) return false

        // Text arrived, so this turn is streaming's to finish. Settle the
        // bubble here rather than only in the Complete branch: a socket can
        // close without a terminal frame - a proxy timing out mid-reply
        // does exactly that - and a `streaming` flag left set is the send
        // button spinning forever with no way back.
        //
        // Any failure after the first chunk is reported, not retried.
        // Re-asking would put the same question to the model twice and show
        // the user two answers to it.
        val error = failure

        _state.update { current ->
            current.copy(
                messages = current.messages.map {
                    if (it.id == messageId) it.copy(streaming = false) else it
                },
                isSending = false,
                error = error ?: current.error,
            )
        }

        return true
    }

    private suspend fun sendOverRest(
        outgoingId: String,
        text: String,
        slowNotice: Job,
        context: JsonObject = JsonObject(emptyMap()),
    ) {

        when (val result = repository.send(text, context)) {

            is AuraResult.Ok -> {
                slowNotice.cancel()
                if (_state.value.isTtsEnabled && result.value.reply.isNotBlank()) {
                    voiceManager?.speak(result.value.reply)
                }
                _state.update { current ->
                    current.copy(
                        messages = current.messages + ChatMessage(
                            id = result.value.messageId,
                            text = result.value.reply,
                            author = ChatMessage.Author.AURA,
                        ),
                        isSending = false,
                        connection = ConnectionState.Connected(
                            (current.connection as? ConnectionState.Connected)
                                ?.provider ?: "aura"
                        ),
                    )
                }
            }

            is AuraResult.Failed -> {
                slowNotice.cancel()
                _state.update { current ->
                    current.copy(
                        messages = current.messages.map { message ->
                            if (message.id == outgoingId) {
                                message.copy(failed = true)
                            } else {
                                message
                            }
                        },
                        isSending = false,
                        error = result.error,
                        connection = ConnectionState.Unavailable(
                            result.error.userMessage
                        ),
                    )
                }
            }
        }
    }

    /**
     * Retry a message that failed, without making the user retype it.
     */
    fun retry(messageId: String) {

        val failed = _state.value.messages.firstOrNull {
            it.id == messageId && it.failed
        } ?: return

        _state.update { current ->
            current.copy(
                messages = current.messages.filterNot { it.id == messageId },
                draft = failed.text,
            )
        }

        send()
    }

    /**
     * Show a companion message that arrived out of band.
     *
     * Called when the user opens the app from a notification, so the thing
     * they tapped is visible in the conversation rather than being a
     * notification that led to an empty screen.
     */
    fun showCompanionMessage(text: String) {

        if (text.isBlank()) return

        if (_state.value.messages.any {
                it.author == ChatMessage.Author.AURA && it.text == text
            }
        ) {
            return
        }

        _state.update { current ->
            current.copy(
                messages = current.messages + ChatMessage(
                    id = UUID.randomUUID().toString(),
                    text = text,
                    author = ChatMessage.Author.AURA,
                )
            )
        }
    }

    fun interruptAgent() {
        cancelCurrentTurn()
    }

    // ------------------------------------------------------------------
    // Voice Engine Controls (TTS & STT & Hands-Free Loop)
    // ------------------------------------------------------------------

    fun toggleTts() {
        val next = !_state.value.isTtsEnabled
        _state.update { it.copy(isTtsEnabled = next) }
        if (!next && voiceManager?.isSpeaking?.value == true) {
            voiceManager.stopSpeaking()
        }
    }

    fun toggleHandsFreeMode() {
        val next = !_state.value.isHandsFreeMode
        _state.update {
            it.copy(
                isHandsFreeMode = next,
                isTtsEnabled = if (next) true else it.isTtsEnabled,
            )
        }
        if (next) {
            startHandsFreeListening()
        } else {
            voiceManager?.stopListening()
            voiceManager?.stopSpeaking()
        }
    }

    fun startHandsFreeListening() {
        if (!_state.value.isHandsFreeMode) return
        voiceManager?.startListening(
            onResult = { recognized ->
                if (!_state.value.isHandsFreeMode) return@startListening
                if (AuraVoiceManager.isExitPhrase(recognized)) {
                    _state.update { it.copy(isHandsFreeMode = false) }
                    speak("Tạm biệt cậu! Khi nào cần tớ cứ gọi nhé.")
                } else {
                    _state.update { it.copy(draft = recognized) }
                    send()
                }
            },
            onError = { _ ->
                // In hands-free loop, if timeout occurs on silence, gently resume listening if still active
                if (_state.value.isHandsFreeMode && !_state.value.isSpeaking && !_state.value.isSending) {
                    viewModelScope.launch {
                        delay(600L)
                        if (_state.value.isHandsFreeMode && !_state.value.isSpeaking && !_state.value.isSending) {
                            startHandsFreeListening()
                        }
                    }
                }
            }
        )
    }

    fun speak(text: String) {
        voiceManager?.speak(text)
    }

    fun stopSpeaking() {
        voiceManager?.stopSpeaking()
    }

    fun startVoiceInput() {
        voiceManager?.startListening(
            onResult = { recognized ->
                _state.update { current ->
                    val combined = if (current.draft.isBlank()) recognized else "${current.draft} $recognized"
                    current.copy(draft = combined)
                }
            },
            onError = { errorMsg ->
                _state.update { it.copy(error = AuraError.Unavailable(errorMsg)) }
            }
        )
    }

    fun stopVoiceInput() {
        voiceManager?.stopListening()
    }

    fun cancelVoiceInput() {
        voiceManager?.cancelListening()
    }

    override fun onCleared() {
        super.onCleared()
        voiceManager?.onSpeechDoneListener = null
        voiceManager?.stopSpeaking()
        voiceManager?.stopListening()
    }


    private var consentCountdownJob: Job? = null

    private fun startToolConsentCountdown(requestId: String, toolName: String, toolDescription: String) {
        consentCountdownJob?.cancel()
        _state.update {
            it.copy(
                pendingToolConsent = ToolConsentState(
                    requestId = requestId,
                    toolName = toolName,
                    toolDescription = toolDescription,
                    secondsRemaining = 30,
                )
            )
        }
        consentCountdownJob = viewModelScope.launch {
            for (sec in 29 downTo 1) {
                delay(1000L)
                _state.update {
                    if (it.pendingToolConsent?.requestId == requestId) {
                        it.copy(pendingToolConsent = it.pendingToolConsent.copy(secondsRemaining = sec))
                    } else it
                }
            }
            delay(1000L)
            if (_state.value.pendingToolConsent?.requestId == requestId) {
                approveToolConsent(requestId)
            }
        }
    }

    fun approveToolConsent(requestId: String) {
        consentCountdownJob?.cancel()
        _state.update { if (it.pendingToolConsent?.requestId == requestId) it.copy(pendingToolConsent = null) else it }
        viewModelScope.launch {
            repository.sendToolConsentResponse(requestId, approved = true)
        }
    }

    fun denyToolConsent(requestId: String) {
        consentCountdownJob?.cancel()
        _state.update { if (it.pendingToolConsent?.requestId == requestId) it.copy(pendingToolConsent = null) else it }
        viewModelScope.launch {
            repository.sendToolConsentResponse(requestId, approved = false)
        }
    }

    companion object {

        /** How long a request may take before we explain the wait. */
        private const val WAKE_NOTICE_MS = 4_000L

        fun factory(
            repository: AuraRepository,
            settings: SettingsProvider,
            transcript: Transcript = Transcript.None,
            isOnline: () -> Boolean = { true },
            context: Context? = null,
            voiceManager: AuraVoiceManager? = null,
        ): ViewModelProvider.Factory = object : ViewModelProvider.Factory {

            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T =
                ChatViewModel(repository, settings, transcript, isOnline, context, voiceManager) as T
        }
    }
}
