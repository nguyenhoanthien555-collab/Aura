package com.aura.companion.work

import android.app.NotificationManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import androidx.core.app.RemoteInput
import com.aura.companion.AuraApplication
import com.aura.companion.R
import com.aura.companion.data.AuraResult
import com.aura.companion.data.chat.Author
import com.aura.companion.data.chat.StoredMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * Handles actionable inline replies directly from Android notification bar without opening the app.
 *
 * Extracts text from [RemoteInput], immediately updates the notification to indicate sending,
 * dispatches the turn to Aura via [com.aura.companion.data.AuraRepository.send], records the
 * conversation in [com.aura.companion.data.chat.Transcript], and updates the notification with Aura's reply.
 */
class DirectReplyReceiver : BroadcastReceiver() {

    companion object {
        const val ACTION_DIRECT_REPLY = "com.aura.companion.ACTION_DIRECT_REPLY"
        const val KEY_TEXT_REPLY = "key_aura_direct_reply"
        const val EXTRA_NOTIFICATION_ID = "extra_notification_id"
    }

    private val scope = CoroutineScope(Dispatchers.IO + SupervisorJob())

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_DIRECT_REPLY) return

        val results = RemoteInput.getResultsFromIntent(intent) ?: return
        val replyText = results.getCharSequence(KEY_TEXT_REPLY)?.toString()?.trim() ?: return
        if (replyText.isBlank()) return

        val notificationId = intent.getIntExtra(EXTRA_NOTIFICATION_ID, 0)
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as? NotificationManager ?: return

        // 1. Immediate visual feedback in notification shade
        val sendingNotification = NotificationCompat.Builder(context, AuraApplication.COMPANION_CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("Aura")
            .setContentText("Bạn: $replyText (Đang gửi...)")
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
        nm.notify(notificationId, sendingNotification)

        val app = context.applicationContext as? AuraApplication ?: return
        val repo = app.container.repository
        val transcript = app.container.transcript

        val pendingResult = goAsync()

        scope.launch {
            try {
                when (val result = repo.send(replyText)) {
                    is AuraResult.Ok -> {
                        val replyMessage = result.value.reply

                        // Append to local persistent transcript
                        try {
                            val existing = transcript.read().messages
                            val userMsg = StoredMessage(
                                id = UUID.randomUUID().toString(),
                                text = replyText,
                                author = Author.USER,
                                timestamp = System.currentTimeMillis(),
                            )
                            val auraMsg = StoredMessage(
                                id = result.value.messageId,
                                text = replyMessage,
                                author = Author.AURA,
                                timestamp = System.currentTimeMillis(),
                            )
                            transcript.write(existing + userMsg + auraMsg)
                        } catch (e: Exception) {
                            // Non-fatal if transcript store fails
                        }

                        val answeredNotification = NotificationCompat.Builder(context, AuraApplication.COMPANION_CHANNEL)
                            .setSmallIcon(R.drawable.ic_notification)
                            .setContentTitle("Aura")
                            .setContentText(replyMessage)
                            .setStyle(NotificationCompat.BigTextStyle().bigText("Bạn: $replyText\n\nAura: $replyMessage"))
                            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                            .setAutoCancel(true)
                            .build()
                        nm.notify(notificationId, answeredNotification)
                    }
                    is AuraResult.Failed -> {
                        val failedNotification = NotificationCompat.Builder(context, AuraApplication.COMPANION_CHANNEL)
                            .setSmallIcon(R.drawable.ic_notification)
                            .setContentTitle("Aura")
                            .setContentText("Không thể gửi tin nhắn (${result.error.userMessage}).")
                            .setPriority(NotificationCompat.PRIORITY_LOW)
                            .setAutoCancel(true)
                            .build()
                        nm.notify(notificationId, failedNotification)
                    }
                }
            } finally {
                pendingResult.finish()
            }
        }
    }
}
