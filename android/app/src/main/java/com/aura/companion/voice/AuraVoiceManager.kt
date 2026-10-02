package com.aura.companion.voice

import android.content.Context
import android.content.Intent
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.net.URLEncoder
import java.util.Locale
import java.util.concurrent.TimeUnit

/**
 * Mobile Voice Engine for Aura Companion.
 * Provides on-device Text-to-Speech (TTS) and Speech-to-Text (STT) for hands-free audio conversation.
 * Optimized for natural Vietnamese (vi-VN) with English (en-US) fallback.
 */
class AuraVoiceManager(
    private val context: Context,
    private val serverUrlProvider: (() -> String)? = null,
    private val tokenProvider: (() -> String)? = null,
) : TextToSpeech.OnInitListener {

    companion object {
        private const val TAG = "AuraVoiceManager"
        private const val UTTERANCE_PREFIX = "aura_voice_"

        /** Clean text for speech synthesis so symbols and markdown don't sound awkward. */
        fun cleanForSpeech(raw: String): String {
            return raw
                // Strip code fences: ```language ... ```
                .replace(Regex("```[\\s\\S]*?```"), " ")
                // Strip inline code: `code`
                .replace(Regex("`[^`]*`"), " ")
                // Strip markdown images: ![alt](url)
                .replace(Regex("!\\[[^\\]]*\\]\\([^)]*\\)"), " ")
                // Convert markdown links: [label](url) -> label
                .replace(Regex("\\[([^\\]]+)\\]\\([^)]*\\)"), "$1")
                // Strip headers, bullet markers, blockquotes, bold/italic markers
                .replace(Regex("[#*~_>|]"), " ")
                // Strip extra consecutive whitespace
                .replace(Regex("\\s+"), " ")
                .trim()
        }

        /** Detects standard exit/goodbye phrases in Vietnamese and English. */
        fun isExitPhrase(phrase: String): Boolean {
            val p = phrase.lowercase(Locale.ROOT).trim()
            val exits = listOf(
                "tạm biệt", "dừng lại", "nghỉ thôi", "dừng cuộc gọi",
                "kết thúc", "goodbye", "stop", "bye bye", "tắt micro",
                "hẹn gặp lại", "thôi nhé", "nghỉ ngơi đi"
            )
            return exits.any { p.contains(it) }
        }
    }


    private val mainHandler = Handler(Looper.getMainLooper())

    // --- Neural Cloud Speech & Local Audio Player ---
    private val voiceScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var activeTtsJob: Job? = null
    private var mediaPlayer: MediaPlayer? = null
    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .build()
    }

    // --- Text-to-Speech (TTS) ---
    private var tts: TextToSpeech? = null
    private var isTtsInitialized = false
    private var activeLocale = Locale("vi", "VN")

    private val _isSpeaking = MutableStateFlow(false)
    val isSpeaking: StateFlow<Boolean> = _isSpeaking.asStateFlow()

    /** Callback invoked when Aura finishes speaking an utterance (with echo mitigation delay). */
    var onSpeechDoneListener: (() -> Unit)? = null

    // --- Speech-to-Text (STT) ---
    private var speechRecognizer: SpeechRecognizer? = null

    private val _isListening = MutableStateFlow(false)
    val isListening: StateFlow<Boolean> = _isListening.asStateFlow()

    private val _rmsDb = MutableStateFlow(0f)
    val rmsDb: StateFlow<Float> = _rmsDb.asStateFlow()

    init {
        initTts()
    }

    private fun initTts() {
        try {
            tts = TextToSpeech(context.applicationContext, this)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to instantiate TextToSpeech", e)
        }
    }

    override fun onInit(status: Int) {
        if (status == TextToSpeech.SUCCESS) {
            val ttsEngine = tts ?: return
            val viLocale = Locale("vi", "VN")
            val viResult = ttsEngine.isLanguageAvailable(viLocale)

            if (viResult >= TextToSpeech.LANG_AVAILABLE) {
                ttsEngine.language = viLocale
                activeLocale = viLocale
                Log.d(TAG, "TTS initialized with Vietnamese locale")
            } else {
                val enLocale = Locale.US
                ttsEngine.language = enLocale
                activeLocale = enLocale
                Log.d(TAG, "Vietnamese not available on device TTS, falling back to US English")
            }

            ttsEngine.setPitch(1.0f)
            ttsEngine.setSpeechRate(1.0f)

            ttsEngine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) {
                    _isSpeaking.value = true
                }

                override fun onDone(utteranceId: String?) {
                    _isSpeaking.value = false
                    mainHandler.postDelayed({
                        onSpeechDoneListener?.invoke()
                    }, 400L)
                }

                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) {
                    _isSpeaking.value = false
                }

                override fun onError(utteranceId: String?, errorCode: Int) {
                    _isSpeaking.value = false
                    Log.w(TAG, "TTS error on utterance: $utteranceId, code: $errorCode")
                }
            })

            isTtsInitialized = true
        } else {
            Log.e(TAG, "TextToSpeech initialization failed with status: $status")
            isTtsInitialized = false
        }
    }

    /**
     * Speaks the given text.
     * Prioritizes high-fidelity Microsoft Edge neural TTS (zh-CN-XiaoxiaoNeural) via server.
     * Automatically and transparently falls back to on-device Android TextToSpeech if offline or on error.
     */
    fun speak(text: String, queueMode: Int = TextToSpeech.QUEUE_FLUSH) {
        val cleaned = cleanForSpeech(text)
        if (cleaned.isBlank()) return

        stopSpeaking()

        val serverUrl = serverUrlProvider?.invoke()?.trimEnd('/')
        if (!serverUrl.isNullOrBlank() && (serverUrl.startsWith("http://") || serverUrl.startsWith("https://"))) {
            activeTtsJob = voiceScope.launch {
                val success = synthesizeAndPlayCloud(cleaned, serverUrl)
                if (!success) {
                    withContext(Dispatchers.Main) {
                        speakNative(cleaned, queueMode)
                    }
                }
            }
        } else {
            speakNative(cleaned, queueMode)
        }
    }

    /**
     * Speaks the given text using Android's native on-device TTS engine.
     */
    fun speakNative(text: String, queueMode: Int = TextToSpeech.QUEUE_FLUSH) {
        if (!isTtsInitialized || tts == null) {
            Log.w(TAG, "Cannot speak natively: TTS not initialized yet")
            return
        }

        val cleaned = cleanForSpeech(text)
        if (cleaned.isBlank()) return

        val utteranceId = "${UTTERANCE_PREFIX}${System.currentTimeMillis()}"
        val params = Bundle().apply {
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 1.0f)
        }

        try {
            tts?.speak(cleaned, queueMode, params, utteranceId)
        } catch (e: Exception) {
            Log.e(TAG, "Exception during tts.speak", e)
            _isSpeaking.value = false
        }
    }

    private suspend fun synthesizeAndPlayCloud(text: String, serverUrl: String): Boolean {
        return withContext(Dispatchers.IO) {
            var tempFile: File? = null
            try {
                val encodedText = URLEncoder.encode(text, "UTF-8")
                val token = tokenProvider?.invoke()?.trim()
                val url = "$serverUrl/api/voice/tts?text=$encodedText&voice=zh-CN-XiaoxiaoNeural"

                val requestBuilder = Request.Builder()
                    .url(url)
                    .get()

                if (!token.isNullOrBlank()) {
                    requestBuilder.addHeader("Authorization", "Bearer $token")
                }

                val response = httpClient.newCall(requestBuilder.build()).execute()
                if (!response.isSuccessful) {
                    Log.w(TAG, "Cloud TTS returned non-200 status: ${response.code}")
                    response.close()
                    return@withContext false
                }

                val body = response.body ?: run {
                    response.close()
                    return@withContext false
                }

                tempFile = File(context.cacheDir, "aura_tts_${System.currentTimeMillis()}.mp3")
                FileOutputStream(tempFile).use { fos ->
                    body.byteStream().use { bis ->
                        bis.copyTo(fos)
                    }
                }
                response.close()

                if (tempFile.length() == 0L) {
                    tempFile.delete()
                    return@withContext false
                }

                val audioFile = tempFile
                withContext(Dispatchers.Main) {
                    playAudioFile(audioFile)
                }
                true
            } catch (e: Exception) {
                Log.w(TAG, "Cloud TTS synthesis failed, will fallback to native TTS: ${e.message}")
                try {
                    tempFile?.delete()
                } catch (_: Exception) {}
                false
            }
        }
    }

    private fun playAudioFile(file: File) {
        try {
            stopSpeaking()
            val mp = MediaPlayer().apply {
                setDataSource(file.absolutePath)
                setAudioAttributes(
                    AudioAttributes.Builder()
                        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                        .setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
                        .build()
                )
                setOnCompletionListener { player ->
                    try {
                        player.release()
                    } catch (_: Exception) {}
                    if (mediaPlayer == player) {
                        mediaPlayer = null
                    }
                    _isSpeaking.value = false
                    try {
                        file.delete()
                    } catch (_: Exception) {}
                    mainHandler.postDelayed({
                        onSpeechDoneListener?.invoke()
                    }, 400L)
                }
                setOnErrorListener { player, what, extra ->
                    Log.w(TAG, "MediaPlayer error: what=$what, extra=$extra")
                    try {
                        player.release()
                    } catch (_: Exception) {}
                    if (mediaPlayer == player) {
                        mediaPlayer = null
                    }
                    _isSpeaking.value = false
                    try {
                        file.delete()
                    } catch (_: Exception) {}
                    true
                }
                prepare()
                start()
            }
            mediaPlayer = mp
            _isSpeaking.value = true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start MediaPlayer for synthesized speech", e)
            try {
                file.delete()
            } catch (_: Exception) {}
            _isSpeaking.value = false
        }
    }

    /** Stops any currently ongoing speech playback immediately. */
    fun stopSpeaking() {
        activeTtsJob?.cancel()
        activeTtsJob = null

        mainHandler.post {
            try {
                mediaPlayer?.let { mp ->
                    if (mp.isPlaying) {
                        mp.stop()
                    }
                    mp.release()
                }
            } catch (e: Exception) {
                Log.e(TAG, "Exception during mediaPlayer stop", e)
            } finally {
                mediaPlayer = null
            }
        }

        try {
            tts?.stop()
        } catch (e: Exception) {
            Log.e(TAG, "Exception during tts.stop", e)
        } finally {
            _isSpeaking.value = false
        }
    }

    /**
     * Activates on-device speech recognition to capture spoken user query.
     */
    fun startListening(
        onResult: (String) -> Unit,
        onError: (String) -> Unit,
    ) {
        mainHandler.post {
            if (!SpeechRecognizer.isRecognitionAvailable(context)) {
                onError("Thiết bị không hỗ trợ nhận diện giọng nói (SpeechRecognizer)")
                return@post
            }

            stopListening()

            try {
                speechRecognizer = SpeechRecognizer.createSpeechRecognizer(context).apply {
                    setRecognitionListener(object : RecognitionListener {
                        override fun onReadyForSpeech(params: Bundle?) {
                            _isListening.value = true
                            _rmsDb.value = 0f
                        }

                        override fun onBeginningOfSpeech() {}

                        override fun onRmsChanged(rmsdB: Float) {
                            _rmsDb.value = rmsdB.coerceAtLeast(0f)
                        }

                        override fun onBufferReceived(buffer: ByteArray?) {}

                        override fun onEndOfSpeech() {
                            _rmsDb.value = 0f
                        }

                        override fun onError(error: Int) {
                            _isListening.value = false
                            _rmsDb.value = 0f
                            try {
                                speechRecognizer?.destroy()
                            } catch (_: Exception) {}
                            speechRecognizer = null
                            val errorMsg = mapSpeechError(error)
                            // Ignore benign "NO_MATCH" if silence
                            if (error != SpeechRecognizer.ERROR_NO_MATCH && error != SpeechRecognizer.ERROR_SPEECH_TIMEOUT) {
                                onError(errorMsg)
                            }
                        }

                        override fun onResults(results: Bundle?) {
                            _isListening.value = false
                            _rmsDb.value = 0f
                            val matches = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                            val recognized = matches?.firstOrNull()?.trim()
                            try {
                                speechRecognizer?.destroy()
                            } catch (_: Exception) {}
                            speechRecognizer = null
                            if (!recognized.isNullOrBlank()) {
                                onResult(recognized)
                            }
                        }

                        override fun onPartialResults(partialResults: Bundle?) {}

                        override fun onEvent(eventType: Int, params: Bundle?) {}
                    })
                }

                val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE, "vi-VN")
                    putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, "vi-VN")
                    putExtra(RecognizerIntent.EXTRA_ONLY_RETURN_LANGUAGE_PREFERENCE, false)
                    putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
                    putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
                }

                speechRecognizer?.startListening(intent)
            } catch (e: Exception) {
                Log.e(TAG, "Failed to start speech recognition", e)
                _isListening.value = false
                onError("Lỗi khởi động micro: ${e.message}")
            }
        }
    }

    /** Stops recording and processes speech input. */
    fun stopListening() {
        mainHandler.post {
            try {
                speechRecognizer?.stopListening()
                speechRecognizer?.destroy()
            } catch (e: Exception) {
                Log.e(TAG, "Error stopping speech recognizer", e)
            } finally {
                speechRecognizer = null
                _isListening.value = false
                _rmsDb.value = 0f
            }
        }
    }

    /** Cancels listening immediately without processing. */
    fun cancelListening() {
        mainHandler.post {
            try {
                speechRecognizer?.cancel()
                speechRecognizer?.destroy()
            } catch (e: Exception) {
                Log.e(TAG, "Error cancelling speech recognizer", e)
            } finally {
                speechRecognizer = null
                _isListening.value = false
                _rmsDb.value = 0f
            }
        }
    }


    private fun mapSpeechError(errorCode: Int): String = when (errorCode) {
        SpeechRecognizer.ERROR_AUDIO -> "Lỗi âm thanh đầu vào"
        SpeechRecognizer.ERROR_CLIENT -> "Lỗi từ client nhận diện"
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Chưa cấp quyền Micro (RECORD_AUDIO)"
        SpeechRecognizer.ERROR_NETWORK -> "Lỗi kết nối mạng khi nhận diện"
        SpeechRecognizer.ERROR_NETWORK_TIMEOUT -> "Hết thời gian chờ mạng"
        SpeechRecognizer.ERROR_NO_MATCH -> "Không nghe rõ câu lệnh"
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "Bộ nhận diện đang bận"
        SpeechRecognizer.ERROR_SERVER -> "Lỗi từ máy chủ nhận diện"
        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "Không nhận thấy giọng nói"
        else -> "Lỗi nhận diện giọng nói (Mã $errorCode)"
    }

    fun destroy() {
        onSpeechDoneListener = null
        stopSpeaking()
        stopListening()
        voiceScope.cancel()
        try {
            tts?.shutdown()
        } catch (e: Exception) {
            Log.e(TAG, "Error shutting down TTS", e)
        } finally {
            tts = null
            isTtsInitialized = false
        }
    }
}
