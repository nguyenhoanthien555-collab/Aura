package com.aura.companion.accessibility

/**
 * Pure parsing for android.wait_for conditions and android.verify
 * checks (PARTS 11, 23).
 *
 * Deliberately free of Android types so a JVM unit test can pin the
 * vocabulary without an accessibility service: the dispatcher hands a
 * parsed condition to the live device, and the tests hold the parser to
 * the same contract the server's tools document.
 *
 * Condition strings are the wire vocabulary the AndroidProvider
 * declares: `foreground=<package>`, `text_exists=<text>`,
   `node_gone=<id>`, `node_exists=<id>`, `activity_changed`.
 */
sealed interface WaitCondition {
    data class Foreground(val value: String) : WaitCondition
    data class TextExists(val value: String) : WaitCondition
    data class NodeExists(val value: String) : WaitCondition
    data class NodeGone(val value: String) : WaitCondition

    /** Proved by comparing fingerprints across samples. */
    object ActivityChanged : WaitCondition
}

object WaitForConditions {

    fun parse(raw: String?): WaitCondition? {
        val trimmed = raw?.trim().orEmpty()
        if (trimmed.isEmpty()) return null

        val kind = trimmed.substringBefore('=')
        val value = trimmed.substringAfter('=', missingDelimiterValue = "")

        return when (kind) {
            "foreground" -> value.takeIf { it.isNotBlank() }
                ?.let { WaitCondition.Foreground(it) }
            "text_exists" -> value.takeIf { it.isNotBlank() }
                ?.let { WaitCondition.TextExists(it) }
            "node_exists" -> value.takeIf { it.isNotBlank() }
                ?.let { WaitCondition.NodeExists(it) }
            "node_gone" -> value.takeIf { it.isNotBlank() }
                ?.let { WaitCondition.NodeGone(it) }
            "activity_changed" -> WaitCondition.ActivityChanged
            else -> null
        }
    }

    /**
     * Evaluate against one sample of current state. `nodeIds` holds the
     * visible node ids, `texts` their concatenated searchable text;
     * both come from one fresh serialization per sample.
     */
    fun evaluate(
        condition: WaitCondition,
        foregroundPackage: String,
        activity: String?,
        nodeIds: Set<String>,
        texts: Set<String>,
        previousActivity: String?,
    ): Boolean = when (condition) {
        is WaitCondition.Foreground ->
            foregroundPackage == condition.value

        is WaitCondition.TextExists ->
            nodeIds.isNotEmpty() && texts.any {
                it.contains(condition.value, ignoreCase = true)
            }

        is WaitCondition.NodeExists -> condition.value in nodeIds

        is WaitCondition.NodeGone -> condition.value !in nodeIds

        is WaitCondition.ActivityChanged ->
            previousActivity != null && activity != previousActivity
    }
}

/**
 * android.verify checks: `package_is=<pkg>`, `text_visible=<text>`,
 * `node_exists=<id>` - evidence, not vibes.
 */
object VerifyChecks {

    const val PACKAGE_IS = "package_is"
    const val TEXT_VISIBLE = "text_visible"
    const val NODE_EXISTS = "node_exists"

    fun parse(raw: String?): Pair<String, String>? {
        val trimmed = raw?.trim().orEmpty()
        if (!trimmed.contains('=')) return null

        val kind = trimmed.substringBefore('=')
        val value = trimmed.substringAfter('=')

        return when (kind) {
            PACKAGE_IS, TEXT_VISIBLE, NODE_EXISTS -> kind to value
            else -> null
        }
    }

    fun evaluate(
        kind: String,
        value: String,
        foregroundPackage: String,
        nodeIds: Set<String>,
        texts: Set<String>,
    ): Boolean = when (kind) {
        PACKAGE_IS -> foregroundPackage == value
        TEXT_VISIBLE -> nodeIds.isNotEmpty() && texts.any {
            it.contains(value, ignoreCase = true)
        }
        NODE_EXISTS -> value in nodeIds
        else -> false
    }
}