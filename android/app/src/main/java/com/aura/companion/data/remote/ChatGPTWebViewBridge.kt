package com.aura.companion.data.remote

import android.annotation.SuppressLint
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.util.UUID

/**
 * Invisible WebView Bridge for ChatGPT Web (GPT-5.6 Luna).
 *
 * Runs inside native Android Chromium engine (android.webkit.WebView) on 4G/Wi-Fi.
 * By executing JavaScript within the authentic browser DOM context of https://chatgpt.com,
 * it naturally passes Cloudflare Turnstile Bytecode and OpenAI Sentinel challenges,
 * eliminating "HTTP 403: Unusual activity has been detected from your device".
 */
@SuppressLint("SetJavaScriptEnabled")
object ChatGPTWebViewBridge {

    private const val CHATGPT_URL = "https://chatgpt.com"
    private const val DEFAULT_USER_AGENT =
        "Mozilla/5.0 (Linux; Android 13; CPH2251) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.88 Mobile Safari/537.36"

    private val mainHandler = Handler(Looper.getMainLooper())
    private var webView: WebView? = null
    private var isInitialized = false
    private var isPageLoaded = false
    private var currentSessionToken: String = ""

    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
    }

    interface StreamCallbacks {
        fun onChunk(chunk: String)
        fun onDone(fullText: String)
        fun onError(error: String)
    }

    private var activeCallbacks: StreamCallbacks? = null

    /**
     * Initializes the invisible WebView on the main thread and primes https://chatgpt.com.
     */
    fun initialize(context: Context, sessionToken: String) {
        if (sessionToken.isBlank()) return
        currentSessionToken = sessionToken

        mainHandler.post {
            try {
                if (webView == null) {
                    val wv = WebView(context.applicationContext)
                    setupWebView(wv, context.applicationContext)
                    webView = wv
                }
                syncCookies(sessionToken)
                if (!isPageLoaded) {
                    webView?.loadUrl(CHATGPT_URL)
                }
                isInitialized = true
            } catch (e: Exception) {
                isInitialized = false
            }
        }
    }

    private fun setupWebView(wv: WebView, context: Context) {
        val settings = wv.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.cacheMode = WebSettings.LOAD_DEFAULT
        settings.userAgentString = DEFAULT_USER_AGENT
        settings.mediaPlaybackRequiresUserGesture = false

        wv.addJavascriptInterface(AuraBridgeInterface(), "AuraBridge")

        wv.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                if (url?.contains("chatgpt.com") == true) {
                    isPageLoaded = true
                }
            }

            override fun onReceivedHttpError(
                view: WebView?,
                request: WebResourceRequest?,
                errorResponse: WebResourceResponse?
            ) {
                super.onReceivedHttpError(view, request, errorResponse)
            }
        }
    }

    private fun syncCookies(sessionToken: String) {
        val cookieManager = CookieManager.getInstance()
        cookieManager.setAcceptCookie(true)
        cookieManager.setAcceptThirdPartyCookies(webView, true)

        val formattedCookie = ChatGPTWebClient.formatCookie(sessionToken)
        val cookies = formattedCookie.split(";").map { it.trim() }
        for (cookie in cookies) {
            if (cookie.isNotBlank()) {
                cookieManager.setCookie(CHATGPT_URL, "$cookie; Path=/; Domain=.chatgpt.com; Secure; SameSite=Lax")
            }
        }
        cookieManager.flush()
    }

    /**
     * JavaScript interface called by injected in-page fetch streaming code.
     */
    private class AuraBridgeInterface {
        @JavascriptInterface
        fun sendChunk(chunk: String) {
            activeCallbacks?.onChunk(chunk)
        }

        @JavascriptInterface
        fun sendDone(fullText: String) {
            val cleaned = ChatGPTWebClient.cleanLunaResponse(fullText)
            activeCallbacks?.onDone(cleaned)
        }

        @JavascriptInterface
        fun sendError(error: String) {
            activeCallbacks?.onError(error)
        }
    }

    /**
     * Executes conversation stream by injecting in-page fetch() in the authenticated DOM.
     */
    suspend fun streamConversation(
        prompt: String,
        sessionToken: String,
        model: String = "auto",
        onChunk: (String) -> Unit
    ): Result<String> = withContext(Dispatchers.IO) {
        val deferredResult = CompletableDeferred<Result<String>>()

        mainHandler.post {
            try {
                if (webView == null || sessionToken != currentSessionToken) {
                    currentSessionToken = sessionToken
                    syncCookies(sessionToken)
                    if (webView == null) {
                        deferredResult.complete(Result.failure(IllegalStateException("WebView chưa được khởi tạo")))
                        return@post
                    }
                }

                activeCallbacks = object : StreamCallbacks {
                    override fun onChunk(chunk: String) {
                        onChunk(chunk)
                    }

                    override fun onDone(fullText: String) {
                        if (!deferredResult.isCompleted) {
                            deferredResult.complete(Result.success(fullText))
                        }
                    }

                    override fun onError(error: String) {
                        if (!deferredResult.isCompleted) {
                            deferredResult.complete(Result.failure(IllegalStateException(error)))
                        }
                    }
                }

                val messageId = UUID.randomUUID().toString()
                val parentId = UUID.randomUUID().toString()
                val escapedPrompt = JsonPrimitive(prompt).toString()

                val jsCode = """
                (async function() {
                    try {
                        let accessToken = "";
                        try {
                            const sessResp = await fetch('/api/auth/session', { credentials: 'include' });
                            if (sessResp.ok) {
                                const sessData = await sessResp.json();
                                accessToken = sessData.accessToken || "";
                            }
                        } catch (e) {}

                        if (!accessToken) {
                            window.AuraBridge.sendError("Không lấy được accessToken từ session cookie");
                            return;
                        }

                        let reqToken = "";
                        try {
                            const reqResp = await fetch('/backend-api/sentinel/chat-requirements', {
                                method: 'POST',
                                headers: {
                                    'Content-Type': 'application/json',
                                    'Authorization': 'Bearer ' + accessToken
                                },
                                credentials: 'include',
                                body: JSON.stringify({ p: "" })
                            });
                            if (reqResp.ok) {
                                const reqData = await reqResp.json();
                                reqToken = reqData.token || "";
                            }
                        } catch (e) {}

                        const convHeaders = {
                            'Content-Type': 'application/json',
                            'Accept': 'text/event-stream',
                            'Authorization': 'Bearer ' + accessToken
                        };
                        if (reqToken) {
                            convHeaders['openai-sentinel-chat-requirements-token'] = reqToken;
                        }

                        const payload = {
                            action: "next",
                            messages: [
                                {
                                    id: "$messageId",
                                    author: { role: "user" },
                                    content: {
                                        content_type: "text",
                                        parts: [$escapedPrompt]
                                    },
                                    metadata: {}
                                }
                            ],
                            parent_message_id: "$parentId",
                            model: "$model",
                            timezone_offset_min: -420,
                            suggestions: [],
                            history_and_training_disabled: false,
                            conversation_mode: { kind: "primary_assistant" },
                            force_paragen: false
                        };

                        const convResp = await fetch('/backend-api/conversation', {
                            method: 'POST',
                            headers: convHeaders,
                            credentials: 'include',
                            body: JSON.stringify(payload)
                        });

                        if (!convResp.ok) {
                            const errBody = await convResp.text();
                            window.AuraBridge.sendError("HTTP " + convResp.status + ": " + errBody);
                            return;
                        }

                        const reader = convResp.body.getReader();
                        const decoder = new TextDecoder();
                        let fullText = "";
                        let lastLength = 0;
                        let buffer = "";

                        while (true) {
                            const { done, value } = await reader.read();
                            if (done) break;
                            buffer += decoder.decode(value, { stream: true });
                            const lines = buffer.split("\n");
                            buffer = lines.pop() || "";

                            for (const rawLine of lines) {
                                const line = rawLine.trim();
                                if (line === "data: [DONE]") {
                                    break;
                                }
                                if (line.startsWith("data: ")) {
                                    try {
                                        const parsed = JSON.parse(line.substring(6));
                                        const msg = parsed.message;
                                        if (msg && msg.author && msg.author.name === "thought") {
                                            continue;
                                        }
                                        const content = msg ? msg.content : null;
                                        if (content && content.content_type === "text" && content.parts && content.parts[0]) {
                                            const curText = content.parts[0];
                                            if (curText.length > lastLength) {
                                                const delta = curText.substring(lastLength);
                                                lastLength = curText.length;
                                                fullText += delta;
                                                window.AuraBridge.sendChunk(delta);
                                            }
                                        }
                                    } catch (err) {}
                                }
                            }
                        }

                        window.AuraBridge.sendDone(fullText);
                    } catch (fatalErr) {
                        window.AuraBridge.sendError("JS Fatal Exception: " + fatalErr.toString());
                    }
                })();
                """.trimIndent()

                webView?.evaluateJavascript(jsCode, null)
            } catch (e: Exception) {
                deferredResult.complete(Result.failure(e))
            }
        }

        deferredResult.await()
    }
}
