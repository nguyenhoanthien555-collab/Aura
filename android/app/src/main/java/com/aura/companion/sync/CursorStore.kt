package com.aura.companion.sync

import java.io.File

/**
 * Persistent storage for the Android sync event cursor.
 * Invariant: Cursor advances ONLY after local event application succeeds!
 * Survives process restarts and app restarts.
 */
interface CursorStore {
    fun getCursor(peerNodeId: String = "RENDER"): Long
    fun setCursor(peerNodeId: String = "RENDER", sequence: Long)
}

class FileCursorStore(private val storageDir: File) : CursorStore {
    private val lock = Any()
    private val memoryCursors = mutableMapOf<String, Long>()

    init {
        if (!storageDir.exists()) {
            storageDir.mkdirs()
        }
    }

    private fun cursorFile(peerNodeId: String): File {
        val safeName = peerNodeId.replace("[^a-zA-Z0-9_-]".toRegex(), "_")
        return File(storageDir, "cursor_$safeName.txt")
    }

    override fun getCursor(peerNodeId: String): Long {
        synchronized(lock) {
            val file = cursorFile(peerNodeId)
            val fromDisk = if (file.exists()) {
                try {
                    file.readText().trim().toLongOrNull()
                } catch (e: Exception) {
                    null
                }
            } else null

            val current = fromDisk ?: memoryCursors[peerNodeId] ?: 0L
            val best = maxOf(current, memoryCursors[peerNodeId] ?: 0L)
            memoryCursors[peerNodeId] = best
            return best
        }
    }

    override fun setCursor(peerNodeId: String, sequence: Long) {
        synchronized(lock) {
            val current = getCursor(peerNodeId)
            // Monotonicity invariant: sequence must never regress
            if (sequence <= current) return

            memoryCursors[peerNodeId] = sequence
            val file = cursorFile(peerNodeId)
            val tmpFile = File(storageDir, "${file.name}.tmp")
            tmpFile.writeText(sequence.toString())
            if (!tmpFile.renameTo(file)) {
                file.writeText(sequence.toString())
                tmpFile.delete()
            }
        }
    }
}
