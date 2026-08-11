package com.aura.companion.work

import android.Manifest
import android.annotation.SuppressLint
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.aura.companion.AuraApplication
import com.aura.companion.MainActivity
import com.aura.companion.R
import com.aura.companion.data.AuraResult
import com.aura.companion.data.remote.NotificationDto
import java.util.concurrent.TimeUnit

/**
 * Collects anything Aura decided to say while the app was closed.
 *
 * Polling rather than push: FCM would need a Google project, a server-side
 * key and a service account, and the brief's first deployment target is a
 * free tier with no such setup. Fifteen minutes is WorkManager's floor for
 * periodic work, and it is the right order of magnitude anyway - a
 * companion that interrupts faster than that is a companion you turn off.
 */
class NotificationWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {

        val app = applicationContext as? AuraApplication ?: return Result.success()

        val settings = app.container.settings.current

        // Every gate is re-checked here, not just at scheduling time: a
        // user who turns notifications off should stop receiving them
        // before the next scheduling pass, not after it.
        if (!settings.notificationsEnabled) return Result.success()
        if (!settings.isConfigured) return Result.success()

        return when (val result = app.container.repository.collectNotifications()) {

            is AuraResult.Ok -> {
                result.value.forEach { present(it) }
                Result.success()
            }

            // Retry covers a server that is asleep or a network that just
            // dropped. WorkManager backs off exponentially, so this does
            // not become a hot loop against a dead host.
            is AuraResult.Failed -> Result.retry()
        }
    }

    /**
     * Post one companion message.
     *
     * The message itself is the notification text. It is written by the
     * companion engine, which is prompted to describe the situation rather
     * than quote the screen - so a password manager on screen produces
     * nothing at all (the server vetoes it), and an ordinary screen
     * produces a remark, not a transcript.
     *
     * The permission is checked at the top and the post itself is wrapped,
     * which covers both halves of what `MissingPermission` asks for. Lint
     * reads neither: the guard is behind a helper, and `runCatching` is an
     * inline function rather than a literal `try`. The suppression is for
     * lint's dataflow, not for the requirement - remove either the guard
     * or the wrapper and this genuinely does throw.
     */
    @SuppressLint("MissingPermission")
    private fun present(notification: NotificationDto) {

        val context = applicationContext

        if (!hasPermission(context)) return

        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra(MainActivity.EXTRA_MESSAGE, notification.message)
        }

        val pending = PendingIntent.getActivity(
            context,
            notification.notificationId.hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val built = NotificationCompat.Builder(context, AuraApplication.COMPANION_CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(context.getString(R.string.app_name))
            .setContentText(notification.message)
            .setStyle(NotificationCompat.BigTextStyle().bigText(notification.message))
            .setPriority(
                if (notification.priority == "high") {
                    NotificationCompat.PRIORITY_DEFAULT
                } else {
                    NotificationCompat.PRIORITY_LOW
                }
            )
            .setAutoCancel(true)
            .setContentIntent(pending)
            .build()

        runCatching {
            NotificationManagerCompat.from(context).notify(
                notification.notificationId.hashCode(),
                built,
            )
        }
    }

    private fun hasPermission(context: Context): Boolean {
        if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.TIRAMISU) {
            return NotificationManagerCompat.from(context).areNotificationsEnabled()
        }
        return ActivityCompat.checkSelfPermission(
            context,
            Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
    }
}

/**
 * Turns the poller on and off to match the user's setting.
 */
object NotificationScheduler {

    private const val WORK_NAME = "aura-notification-poll"

    fun sync(context: Context, enabled: Boolean) {
        if (enabled) start(context) else stop(context)
    }

    private fun start(context: Context) {

        val request = PeriodicWorkRequestBuilder<NotificationWorker>(
            15, TimeUnit.MINUTES,
        )
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .build()

        WorkManager.getInstance(context).enqueueUniquePeriodicWork(
            WORK_NAME,
            // KEEP, not UPDATE: replacing the request on every launch would
            // reset its period, and an app opened often would then never
            // reach the fifteen-minute mark and never poll at all.
            ExistingPeriodicWorkPolicy.KEEP,
            request,
        )
    }

    private fun stop(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(WORK_NAME)
    }
}
