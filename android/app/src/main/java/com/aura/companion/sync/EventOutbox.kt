package com.aura.companion.sync

import com.aura.companion.data.remote.SyncEventDto
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File

enum class OutboxEventStatus {
    PENDING,
    SENDING,
    ACKNOWLEDGED,
    QUARANTINED
}

@Serializable
data class OutboxRecord(
    val event: SyncEventDto,
    var status: String = OutboxEventStatus.PENDING.name,
    var attempts: Int = 0,
    var lastError: String = "",
    val enqueuedAt: Long = System.currentTimeMillis(),
    var acknowledgedAt: Long? = null
)

interface EventOutbox {
    fun enqueue(event: SyncEventDto)
    fun getPending(limit: Int = 50): List<SyncEventDto>
    fun markSending(eventIds: List<String>)
    fun markAcknowledged(eventIds: List<String>)
    fun markRetry(eventIds: List<String>, error: String)
    fun markQuarantined(eventId: String, reason: String)
    fun pendingCount(): Int
    fun pruneAcknowledged(maxToKeep: Int = 5000): Int
}

class FileEventOutbox(private val storageDir: File) : EventOutbox {
    private val lock = Any()
    private val records = LinkedHashMap<String, OutboxRecord>()
    private val journalFile = File(storageDir, "outbox_journal.jsonl")
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }

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
                                val rec = json.decodeFromString(OutboxRecord.serializer(), line.trim())
                                if (rec.status == OutboxEventStatus.SENDING.name) {
                                    rec.status = OutboxEventStatus.PENDING.name
                                }
                                records[rec.event.eventId] = rec
                            } catch (e: Exception) {
                                // Skip and quarantine corrupted line without losing subsequent valid lines
                            }
                        }
                    }
                } catch (e: Exception) {
                    // Start from recovered state
                }
            }
        }
    }

    private fun flushJournal() {
        val tmpFile = File(storageDir, "outbox_journal.tmp")
        tmpFile.bufferedWriter().use { writer ->
            for (rec in records.values) {
                writer.write(json.encodeToString(OutboxRecord.serializer(), rec))
                writer.newLine()
            }
        }
        if (!tmpFile.renameTo(journalFile)) {
            journalFile.delete()
            tmpFile.renameTo(journalFile)
        }
    }

    override fun enqueue(event: SyncEventDto) {
        synchronized(lock) {
            if (!records.containsKey(event.eventId)) {
                val rec = OutboxRecord(event = event, status = OutboxEventStatus.PENDING.name)
                records[event.eventId] = rec
                journalFile.appendText(json.encodeToString(OutboxRecord.serializer(), rec) + "\n")
            }
        }
    }

    override fun getPending(limit: Int): List<SyncEventDto> {
        synchronized(lock) {
            return records.values
                .filter { it.status == OutboxEventStatus.PENDING.name || it.status == OutboxEventStatus.SENDING.name }
                .take(limit)
                .map { it.event }
        }
    }

    override fun markSending(eventIds: List<String>) {
        synchronized(lock) {
            var changed = false
            for (id in eventIds) {
                records[id]?.let {
                    it.status = OutboxEventStatus.SENDING.name
                    it.attempts += 1
                    changed = true
                }
            }
            if (changed) flushJournal()
        }
    }

    override fun markAcknowledged(eventIds: List<String>) {
        synchronized(lock) {
            var changed = false
            val now = System.currentTimeMillis()
            for (id in eventIds) {
                records[id]?.let {
                    it.status = OutboxEventStatus.ACKNOWLEDGED.name
                    it.acknowledgedAt = now
                    changed = true
                }
            }
            if (changed) flushJournal()
        }
    }

    override fun markRetry(eventIds: List<String>, error: String) {
        synchronized(lock) {
            var changed = false
            for (id in eventIds) {
                records[id]?.let {
                    it.status = OutboxEventStatus.PENDING.name
                    it.lastError = error
                    changed = true
                }
            }
            if (changed) flushJournal()
        }
    }

    override fun markQuarantined(eventId: String, reason: String) {
        synchronized(lock) {
            records[eventId]?.let {
                it.status = OutboxEventStatus.QUARANTINED.name
                it.lastError = reason
                flushJournal()
            }
        }
    }

    override fun pendingCount(): Int {
        synchronized(lock) {
            return records.values.count {
                it.status == OutboxEventStatus.PENDING.name || it.status == OutboxEventStatus.SENDING.name
            }
        }
    }

    override fun pruneAcknowledged(maxToKeep: Int): Int {
        synchronized(lock) {
            val acked = records.values.filter { it.status == OutboxEventStatus.ACKNOWLEDGED.name }
            if (acked.size <= maxToKeep) return 0
            val toRemove = acked.take(acked.size - maxToKeep)
            for (r in toRemove) {
                records.remove(r.event.eventId)
            }
            flushJournal()
            return toRemove.size
        }
    }
}
