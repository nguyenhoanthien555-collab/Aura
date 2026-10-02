package com.aura.companion

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.chat.TranscriptStore
import com.aura.companion.data.settings.SettingsStore
import com.aura.companion.sync.CursorStore
import com.aura.companion.sync.EventInbox
import com.aura.companion.sync.EventOutbox
import com.aura.companion.sync.FileCursorStore
import com.aura.companion.sync.FileEventInbox
import com.aura.companion.sync.FileEventOutbox
import com.aura.companion.sync.SyncClient
import java.io.File

/**
 * The object graph.
 *
 * Hand-wired rather than Hilt: the graph is three objects deep and a
 * dependency-injection framework would add an annotation processor to the
 * build for no benefit at this size. If it grows past this, swap it - the
 * call sites all go through [container].
 */
class AuraApplication : Application() {

    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        createNotificationChannel()
    }

    /**
     * The channel companion messages arrive on.
     *
     * Created at startup because posting to a channel that does not exist
     * silently drops the notification on Android 8+. Importance is
     * DEFAULT, not HIGH: an unprompted remark is not an alarm, and a
     * heads-up banner for every observation is exactly the spam the brief
     * rules out.
     */
    private fun createNotificationChannel() {

        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return

        val channel = NotificationChannel(
            COMPANION_CHANNEL,
            getString(R.string.channel_companion),
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = getString(R.string.channel_companion_description)
            enableVibration(false)
        }

        val manager = getSystemService(NotificationManager::class.java)
        manager?.createNotificationChannel(channel)
    }

    companion object {
        const val COMPANION_CHANNEL = "aura_companion"
    }
}

/**
 * Everything that outlives a screen.
 */
class AppContainer(application: Application) {

    val settings: SettingsStore by lazy { SettingsStore(application) }

    /**
     * The conversation, across launches.
     *
     * One object serving both halves of §15: the chat screen reads its
     * bubbles from it and the repository reads the session id behind them.
     * Two stores would let the two drift apart, which is the failure worth
     * avoiding - a restored transcript beside a session the server has never
     * heard of.
     */
    val transcript: TranscriptStore by lazy { TranscriptStore(application) }

    val repository: AuraRepository by lazy { AuraRepository(settings, transcript) }

    val syncOutbox: EventOutbox by lazy {
        FileEventOutbox(File(application.filesDir, "sync/outbox"))
    }
    val syncInbox: EventInbox by lazy {
        FileEventInbox(File(application.filesDir, "sync/inbox"))
    }
    val cursorStore: CursorStore by lazy {
        FileCursorStore(File(application.filesDir, "sync/cursor"))
    }
    val context: android.content.Context = application.applicationContext
    private val appContext get() = context

    val voiceManager: com.aura.companion.voice.AuraVoiceManager by lazy {
        com.aura.companion.voice.AuraVoiceManager(
            context = application,
            serverUrlProvider = { settings.current.serverUrl },
            tokenProvider = { settings.current.authToken }
        )
    }

    /**
     * Is there a network that can actually carry a request right now?
     *
     * Used by the hybrid chat brain to decide, per turn, whether to reach for
     * the smart cloud server or answer on-device. Checks for a validated
     * internet capability rather than merely "a network exists", so captive
     * portals and a connected-but-dead Wi-Fi fall back to local instead of
     * hanging. Any failure reading connectivity is treated as offline — the
     * safe default is the model that needs nothing.
     */
    fun isOnline(): Boolean = try {
        val cm = appContext.getSystemService(android.net.ConnectivityManager::class.java)
        val network = cm?.activeNetwork ?: return false
        val caps = cm.getNetworkCapabilities(network) ?: return false
        caps.hasCapability(android.net.NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
            caps.hasCapability(android.net.NetworkCapabilities.NET_CAPABILITY_VALIDATED)
    } catch (e: Exception) {
        false
    }

    val syncClient: SyncClient by lazy {
        SyncClient(
            api = { repository.api() ?: throw IllegalStateException("Server not configured") },
            settings = settings,
            outbox = syncOutbox,
            inbox = syncInbox,
            cursorStore = cursorStore,
        )
    }
}
