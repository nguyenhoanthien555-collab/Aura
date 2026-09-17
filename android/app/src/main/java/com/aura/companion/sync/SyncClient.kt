package com.aura.companion.sync

import android.util.Log
import com.aura.companion.data.remote.AckEventsRequestDto
import com.aura.companion.data.remote.AuraApi
import com.aura.companion.data.remote.PushEventsRequestDto
import com.aura.companion.data.remote.RegisterNodeRequestDto
import com.aura.companion.data.remote.SyncEventDto
import com.aura.companion.data.settings.SettingsProvider
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.File
import java.io.IOException

/**
 * Native Android Sync Client.
 * Orchestrates:
 * 1. Node registration
 * 2. Pushing pending outbox events to Render relay (/api/sync/events/push)
 * 3. Pulling incoming events from Render relay (/api/sync/events/pull)
 * 4. Applying events locally with deduplication
 * 5. Advancing cursor ONLY after successful local event application
 * 6. Acknowledging delivered events (/api/sync/events/ack)
 * 7. Offline/timeout resilience: events are NEVER discarded on network drops.
 */
class SyncClient(
    private val api: () -> AuraApi,
    private val settings: SettingsProvider,
    private val outbox: EventOutbox,
    private val inbox: EventInbox,
    private val cursorStore: CursorStore,
    private val eventDispatcher: ((SyncEventDto) -> Boolean)? = null,
) {
    private var registered = false
    private val syncMutex = Mutex()

    val nodeId: String
        get() = settings.current.deviceId.ifBlank { "android-unknown" }

    suspend fun registerNode(): Boolean {
        if (!settings.current.isConfigured) return false
        try {
            val res = api().registerNode(
                RegisterNodeRequestDto(
                    nodeId = nodeId,
                    nodeType = "ANDROID",
                    installationId = nodeId
                )
            )
            if (res.isSuccessful && res.body()?.status == "ok") {
                registered = true
                return true
            }
        } catch (e: Exception) {
            Log.w(TAG, "Sync registration failed (will retry): ${e.message}")
        }
        return false
    }

    suspend fun pushPending(): Int {
        if (!settings.current.isConfigured) return 0
        val pending = outbox.getPending(50)
        if (pending.isEmpty()) return 0

        val eventIds = pending.map { it.eventId }
        outbox.markSending(eventIds)

        try {
            val res = api().pushEvents(
                PushEventsRequestDto(
                    nodeId = nodeId,
                    events = pending
                )
            )
            if (res.isSuccessful) {
                val body = res.body()
                val acked = body?.acknowledged ?: emptyList()
                if (acked.isNotEmpty()) {
                    outbox.markAcknowledged(acked)
                }
                val conflicts = body?.conflicts ?: emptyList()
                for (conf in conflicts) {
                    val eid = conf["event_id"]?.toString()?.replace("\"", "")
                    val err = conf["error"]?.toString()?.replace("\"", "") ?: "Conflict"
                    if (!eid.isNullOrBlank()) {
                        outbox.markQuarantined(eid, err)
                    }
                }
                return acked.size
            } else {
                outbox.markRetry(eventIds, "HTTP ${res.code()}")
            }
        } catch (e: Exception) {
            Log.w(TAG, "Push failed, preserving events in outbox: ${e.message}")
            outbox.markRetry(eventIds, e.message ?: "Network error")
        }
        return 0
    }

    suspend fun pullIncoming(): Int {
        if (!settings.current.isConfigured) return 0
        val cursor = cursorStore.getCursor("RENDER")

        try {
            val res = api().pullEvents(
                nodeId = nodeId,
                afterSequence = cursor,
                limit = 100
            )
            if (!res.isSuccessful) return 0

            val body = res.body() ?: return 0
            val events = body.events
            if (events.isEmpty()) return 0

            val successfullyApplied = mutableListOf<String>()
            var latestSeq = cursor

            for (event in events) {
                val check = inbox.shouldProcess(event)
                when (check) {
                    is InboxApplyResult.IdempotentDuplicate -> {
                        // Already applied -> mark for server ACK and cursor update
                        successfullyApplied.add(event.eventId)
                        if (event.logicalSequence > latestSeq) {
                            latestSeq = event.logicalSequence
                            cursorStore.setCursor("RENDER", latestSeq)
                        }
                    }
                    is InboxApplyResult.Conflict -> {
                        Log.w(TAG, "Quarantining incoming conflict: ${check.reason}")
                    }
                    is InboxApplyResult.Applied -> {
                        // Apply locally
                        val applied = eventDispatcher?.invoke(event) ?: true
                        if (applied) {
                            inbox.recordApplied(event)
                            successfullyApplied.add(event.eventId)
                            // CRITICAL INVARIANT: Advance cursor ONLY after application
                            if (event.logicalSequence > latestSeq) {
                                latestSeq = event.logicalSequence
                                cursorStore.setCursor("RENDER", latestSeq)
                            }
                        } else {
                            Log.w(TAG, "Event dispatch rejected by local domain: ${event.eventId}")
                            break // Stop stream to preserve strict ordering
                        }
                    }
                }
            }

            // Acknowledge back to server
            if (successfullyApplied.isNotEmpty()) {
                try {
                    api().ackEvents(
                        AckEventsRequestDto(
                            nodeId = nodeId,
                            eventIds = successfullyApplied
                        )
                    )
                } catch (e: Exception) {
                    Log.w(TAG, "ACK send failed (server will re-deliver idempotently): ${e.message}")
                }
            }

            return successfullyApplied.size
        } catch (e: Exception) {
            Log.w(TAG, "Pull failed: ${e.message}")
            return 0
        }
    }

    suspend fun syncCycle(): Pair<Int, Int> = syncMutex.withLock {
        if (!registered) {
            registerNode()
        }
        val pushed = pushPending()
        val pulled = pullIncoming()
        Pair(pushed, pulled)
    }

    companion object {
        private const val TAG = "AuraSyncClient"
    }
}
