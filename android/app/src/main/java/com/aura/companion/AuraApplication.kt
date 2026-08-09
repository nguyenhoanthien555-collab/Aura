package com.aura.companion

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.settings.SettingsStore

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

    val repository: AuraRepository by lazy { AuraRepository(settings) }
}
