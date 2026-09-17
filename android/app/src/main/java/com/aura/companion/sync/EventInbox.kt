package com.aura.companion.sync

import com.aura.companion.data.remote.SyncEventDto
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File

sealed interface InboxApplyResult {
    object Applied : InboxApplyResult
    object IdempotentDuplicate : InboxApplyResult
    data class Conflict(val reason: String) : InboxApplyResult
}

@Serializable
data class InboxRecord(
    val eventId: String,
    val originNodeId: String,
    val payloadHash: String,
    val appliedAt: Long = System.currentTimeMillis()
)

interface EventInbox {
    fun shouldProcess(event: SyncEventDto): InboxApplyResult
    fun recordApplied(event: SyncEventDto)
    fun processedCount(): Int
}

class FileEventInbox(private val storageDir: File) : EventInbox {
    private val lock = Any()
    private val processed = LinkedHashMap<String, InboxRecord>()
    private val journalFile = File(storageDir, "inbox_journal.jsonl")
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

    override fun processedCount(): Int = synchronized(lock) { processed.size }

    init {
        if (!storageDir.exists()) {
            storageDir.mkdirs()
        }
        loadJournal()
    }

    private fun loadJournal() {
        synchronized(lock) {
            if (journalFile.exists()) {
                try {
                    journalFile.forEachLine { line ->
                        if (line.isNotBlank()) {
                            try {
                                val rec = json.decodeFromString(InboxRecord.serializer(), line.trim())
                                processed[rec.eventId] = rec
                            } catch (e: Exception) {
                                // Skip corrupted line
                            }
                        }
                    }
                } catch (e: Exception) {
                    // Start from loaded
                }
            }
        }
    }

    override fun shouldProcess(event: SyncEventDto): InboxApplyResult {
        synchronized(lock) {
            // 1. Verify payload hash
            val computed = CanonicalJson.computeHash(event.payload)
            if (event.payloadHash.isNotBlank() && event.payloadHash != computed) {
                return InboxApplyResult.Conflict("Payload hash mismatch: expected $computed but got ${event.payloadHash}")
            }

            // 2. Check if already processed
            val existing = processed[event.eventId]
            if (existing != null) {
                return if (existing.payloadHash == computed || existing.payloadHash == event.payloadHash) {
                    InboxApplyResult.IdempotentDuplicate
                } else {
                    InboxApplyResult.Conflict("Existing event ID with differing hash: ${existing.payloadHash} vs $computed")
                }
            }

            return InboxApplyResult.Applied
        }
    }

    override fun recordApplied(event: SyncEventDto) {
        synchronized(lock) {
            val hash = if (event.payloadHash.isNotBlank()) event.payloadHash else CanonicalJson.computeHash(event.payload)
            val rec = InboxRecord(
                eventId = event.eventId,
                originNodeId = event.originNodeId,
                payloadHash = hash
            )
            processed[event.eventId] = rec
            journalFile.appendText(json.encodeToString(InboxRecord.serializer(), rec) + "\n")
        }
    }
}
