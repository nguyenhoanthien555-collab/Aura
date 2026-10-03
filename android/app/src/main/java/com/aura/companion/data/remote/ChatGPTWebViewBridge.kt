package com.aura.companion.data.remote

import android.annotation.SuppressLint
import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.webkit.ConsoleMessage
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonPrimitive

/**
 * Invisible WebView DOM Bridge for ChatGPT Web (GPT-5.6 Luna).
 *
 * Runs inside native Android Chromium engine (android.webkit.WebView) on 4G/Wi-Fi.
 * Operates by interacting directly with the authenticated ChatGPT Web DOM:
 * 1. Primes https://chatgpt.com/ with session tokens.
 * 2. Injects user prompts into the ProseMirror editor (#prompt-textarea).
 * 3. Triggers the official send button, allowing ChatGPT's own client-side JavaScript
 *    to handle Cloudflare Turnstile token generation and OpenAI Sentinel challenges natively.
 * 4. Extracts streaming tokens directly from the assistant response article in real-time.
 *
 * Completely eliminates HTTP 403 Forbidden ("Unusual activity has been detected from your device").
 */
@SuppressLint("SetJavaScriptEnabled")
object ChatGPTWebViewBridge {

    private const val TAG = "ChatGPTWebViewBridge"
    private const val CHATGPT_URL = "https://chatgpt.com/"
    private const val DEFAULT_USER_AGENT =
        "Mozilla/5.0 (Linux; Android 13; CPH2251) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.6613.88 Mobile Safari/537.36"

    private val mainHandler = Handler(Looper.getMainLooper())
    private var webView: WebView? = null
    private var isInitialized = false
    private var isPageLoaded = false
    private var currentSessionToken: String = ""

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
                    Log.i(TAG, "Creating WebView instance on main thread")
                    val wv = WebView(context.applicationContext)
                    setupWebView(wv)
                    webView = wv
                }
                syncCookies(sessionToken)
                if (!isPageLoaded) {
                    Log.i(TAG, "Loading initial URL: $CHATGPT_URL")
                    webView?.loadUrl(CHATGPT_URL)
                }
                isInitialized = true
            } catch (e: Exception) {
                Log.e(TAG, "Failed to initialize WebView", e)
                isInitialized = false
            }
        }
    }

    private fun setupWebView(wv: WebView) {
        try {
            WebView.setWebContentsDebuggingEnabled(true)
        } catch (_: Exception) {}

        val settings = wv.settings
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.databaseEnabled = true
        settings.cacheMode = WebSettings.LOAD_DEFAULT
        settings.userAgentString = DEFAULT_USER_AGENT
        settings.mediaPlaybackRequiresUserGesture = false

        wv.addJavascriptInterface(AuraBridgeInterface(), "AuraBridge")

        wv.webChromeClient = object : WebChromeClient() {
            override fun onConsoleMessage(consoleMessage: ConsoleMessage?): Boolean {
                val level = consoleMessage?.messageLevel() ?: ConsoleMessage.MessageLevel.LOG
                val msg = consoleMessage?.message() ?: ""
                val line = consoleMessage?.lineNumber() ?: 0
                val src = consoleMessage?.sourceId() ?: ""
                Log.d(TAG, "JS [$level] $msg ($src:$line)")
                return true
            }
        }

        wv.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                Log.i(TAG, "onPageFinished: $url")
                if (url?.contains("chatgpt.com") == true) {
                    isPageLoaded = true
                }
            }

            override fun onReceivedError(
                view: WebView?,
                request: WebResourceRequest?,
                error: WebResourceError?
            ) {
                super.onReceivedError(view, request, error)
                if (request?.isForMainFrame == true) {
                    Log.w(TAG, "Main frame error: ${error?.description} (${request.url})")
                }
            }

            override fun onReceivedHttpError(
                view: WebView?,
                request: WebResourceRequest?,
                errorResponse: WebResourceResponse?
            ) {
                super.onReceivedHttpError(view, request, errorResponse)
                if (request?.isForMainFrame == true) {
                    Log.w(TAG, "Main frame HTTP error: ${errorResponse?.statusCode} (${request.url})")
                }
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
        Log.i(TAG, "Synced session cookies to CookieManager for $CHATGPT_URL")
    }

    /**
     * JavaScript interface called by injected DOM streaming script.
     */
    private class AuraBridgeInterface {
        @JavascriptInterface
        fun sendChunk(chunk: String) {
            activeCallbacks?.onChunk(chunk)
        }

        @JavascriptInterface
        fun sendDone(fullText: String) {
            val cleaned = ChatGPTWebClient.cleanLunaResponse(fullText)
            Log.i(TAG, "Response completed: ${fullText.length} chars (cleaned: ${cleaned.length} chars)")
            activeCallbacks?.onDone(cleaned)
        }

        @JavascriptInterface
        fun sendError(error: String) {
            Log.e(TAG, "Bridge received error: $error")
            activeCallbacks?.onError(error)
        }
    }

    /**
     * Executes conversation turn by interacting with ChatGPT Web DOM.
     * Operates natively in Chromium, bypassing Cloudflare Turnstile & Sentinel PoW.
     */
    suspend fun streamConversation(
        prompt: String,
        sessionToken: String,
        model: String = "auto",
        onChunk: (String) -> Unit
    ): Result<String> = withContext(Dispatchers.IO) {
        val deferredResult = CompletableDeferred<Result<String>>()
        Log.i(TAG, "streamConversation called (prompt length: ${prompt.length})")

        mainHandler.post {
            try {
                if (webView == null || sessionToken != currentSessionToken) {
                    currentSessionToken = sessionToken
                    syncCookies(sessionToken)
                    if (webView == null) {
                        Log.e(TAG, "WebView is null during streamConversation")
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

                val escapedPrompt = JsonPrimitive(prompt).toString()

                val jsCode = """
                (function() {
                    try {
                        let attempts = 0;
                        function checkAndSend() {
                            const el = document.querySelector('#prompt-textarea');
                            if (!el) {
                                attempts++;
                                if (attempts > 60) {
                                    window.AuraBridge.sendError("Timeout waiting for #prompt-textarea in ChatGPT DOM");
                                    return;
                                }
                                setTimeout(checkAndSend, 200);
                                return;
                            }

                            // Dismiss any modal dialogs
                            try {
                                document.querySelectorAll("button[aria-label='Close']").forEach(b => b.click());
                                Array.from(document.querySelectorAll('button')).forEach(b => {
                                    if ((b.innerText || '').includes('Stay logged out')) b.click();
                                });
                            } catch (e) {}

                            const priorArticles = document.querySelectorAll('article, [data-message-author-role="assistant"]');
                            const targetIdx = priorArticles.length;

                            el.focus();
                            const rawPrompt = $escapedPrompt;
                            const lines = rawPrompt.split('\n');
                            el.innerHTML = lines.map(l => '<p>' + (l.length > 0 ? l.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') : '<br>') + '</p>').join('');
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));

                            setTimeout(() => {
                                const sendBtn = document.querySelector('button[data-testid="send-button"], button[aria-label*="Send"], button[data-testid*="send"]');
                                if (!sendBtn) {
                                    window.AuraBridge.sendError("Send button not found in ChatGPT DOM");
                                    return;
                                }
                                sendBtn.click();

                                let lastLen = 0;
                                let idleTicks = 0;
                                let fullText = "";
                                let waitAssistantTicks = 0;

                                const intervalId = setInterval(() => {
                                    const currentArticles = document.querySelectorAll('article, [data-message-author-role="assistant"]');
                                    const target = currentArticles.length > targetIdx ? currentArticles[targetIdx] : null;
                                    const stopBtn = document.querySelector('button[data-testid="stop-button"]');
                                    const sendBtnBack = document.querySelector('button[data-testid="send-button"], button[aria-label*="Send"], button[data-testid*="send"]');

                                    if (!target) {
                                        waitAssistantTicks++;
                                        if (waitAssistantTicks > 350) { // ~28 seconds
                                            clearInterval(intervalId);
                                            window.AuraBridge.sendError("Timeout waiting for assistant response in DOM");
                                        }
                                        return;
                                    }

                                    const curText = target.innerText || "";
                                    if (curText.length > lastLen) {
                                        const delta = curText.substring(lastLen);
                                        lastLen = curText.length;
                                        fullText = curText;
                                        idleTicks = 0;
                                        window.AuraBridge.sendChunk(delta);
                                    } else {
                                        idleTicks++;
                                    }

                                    // Completion: No stop button, some text generated, and idle for >= 30 ticks (~2.4s)
                                    // or send button returned and idle for >= 15 ticks (~1.2s)
                                    const isDoneGenerating = !stopBtn && lastLen > 0 && (idleTicks >= 30 || (sendBtnBack && idleTicks >= 15));
                                    if (isDoneGenerating || idleTicks > 450) {
                                        clearInterval(intervalId);
                                        window.AuraBridge.sendDone(fullText);
                                    }
                                }, 80);
                            }, 150);
                        }

                        checkAndSend();
                    } catch (fatalErr) {
                        window.AuraBridge.sendError("DOM Bridge Fatal: " + fatalErr.toString());
                    }
                })();
                """.trimIndent()

                webView?.evaluateJavascript(jsCode, null)
            } catch (e: Exception) {
                Log.e(TAG, "Exception in streamConversation", e)
                deferredResult.complete(Result.failure(e))
            }
        }

        deferredResult.await()
    }
}
