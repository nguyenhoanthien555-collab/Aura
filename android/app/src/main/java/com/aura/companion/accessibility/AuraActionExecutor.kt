package com.aura.companion.accessibility

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo
import android.util.Log

/**
 * Sealed result type for gesture dispatch, distinguishing between
 * rejection, completion, cancellation, timeout, and exceptions.
 */
sealed class GestureResult {
    object Completed : GestureResult()
    object Cancelled : GestureResult()
    object DispatchRejected : GestureResult()
    object Timeout : GestureResult()
    class Error(val exception: Exception) : GestureResult()

    val isSuccess: Boolean get() = this is Completed

    override fun toString(): String = when (this) {
        is Completed -> "COMPLETED"
        is Cancelled -> "CANCELLED"
        is DispatchRejected -> "DISPATCH_REJECTED"
        is Timeout -> "TIMEOUT"
        is Error -> "ERROR(${exception.message})"
    }
}


class AuraActionExecutor(
    private val service: AccessibilityService
) {

    fun resolveNode(
        targetNodeId: String,
        oldTree: Map<String, AccessibilityNode>,
        currentRoot: AccessibilityNodeInfo,
        freshNodeMap: MutableMap<String, AccessibilityNodeInfo>
    ): Pair<AccessibilityNodeInfo?, AccessibilityNode?> {
        val targetNodeMeta = oldTree[targetNodeId] ?: return Pair(null, null)

        // 1. Serialize the current tree to find fresh node mapping
        val freshTree = AccessibilityNodeSerializer.serialize(currentRoot, freshNodeMap)

        // 2. Check if the same ID in freshTree is a good match
        val freshNodeMetaAtId = freshTree[targetNodeId]
        if (freshNodeMetaAtId != null && isMatch(targetNodeMeta, freshNodeMetaAtId)) {
            val nodeInfo = freshNodeMap[targetNodeId]
            if (nodeInfo != null) {
                Log.d("AuraActionExecutor", "Resolved $targetNodeId by exact ID match.")
                return Pair(nodeInfo, freshNodeMetaAtId)
            }
        }

        // 3. Search the entire freshTree for the best match
        var bestNodeId: String? = null
        var bestScore = -1.0

        for ((freshId, freshNodeMeta) in freshTree) {
            val score = calculateMatchScore(targetNodeMeta, freshNodeMeta)
            if (score > bestScore && score >= 0.5) {
                bestScore = score
                bestNodeId = freshId
            }
        }

        if (bestNodeId != null) {
            val nodeInfo = freshNodeMap[bestNodeId]
            if (nodeInfo != null) {
                Log.d("AuraActionExecutor", "Resolved $targetNodeId to equivalent node $bestNodeId (score: $bestScore).")
                return Pair(nodeInfo, freshTree[bestNodeId])
            }
        }

        Log.w("AuraActionExecutor", "Could not resolve node $targetNodeId in the current tree.")
        return Pair(null, null)
    }

    private fun isMatch(a: AccessibilityNode, b: AccessibilityNode): Boolean {
        if (a.className != b.className) return false
        if (a.text != null && a.text != b.text) return false
        if (a.contentDescription != null && a.contentDescription != b.contentDescription) return false
        return boundsOverlapOrClose(a.bounds, b.bounds)
    }

    private fun boundsOverlapOrClose(b1: List<Int>, b2: List<Int>): Boolean {
        if (b1.size < 4 || b2.size < 4) return false
        val leftDiff = Math.abs(b1[0] - b2[0])
        val topDiff = Math.abs(b1[1] - b2[1])
        val rightDiff = Math.abs(b1[2] - b2[2])
        val bottomDiff = Math.abs(b1[3] - b2[3])
        return leftDiff < 100 && topDiff < 100 && rightDiff < 100 && bottomDiff < 100
    }

    private fun calculateMatchScore(a: AccessibilityNode, b: AccessibilityNode): Double {
        if (a.className != b.className) return 0.0

        var score = 0.0
        var totalWeight = 0.0

        if (a.text != null) {
            totalWeight += 2.0
            if (a.text == b.text) {
                score += 2.0
            }
        }

        if (a.contentDescription != null) {
            totalWeight += 2.0
            if (a.contentDescription == b.contentDescription) {
                score += 2.0
            }
        }

        if (a.bounds.size >= 4 && b.bounds.size >= 4) {
            totalWeight += 1.0
            val aCx = (a.bounds[0] + a.bounds[2]) / 2
            val aCy = (a.bounds[1] + a.bounds[3]) / 2
            val bCx = (b.bounds[0] + b.bounds[2]) / 2
            val bCy = (b.bounds[1] + b.bounds[3]) / 2
            val dist = Math.sqrt(Math.pow((aCx - bCx).toDouble(), 2.0) + Math.pow((aCy - bCy).toDouble(), 2.0))
            if (dist < 100) {
                score += 1.0
            } else if (dist < 300) {
                score += 0.5
            }
        }

        totalWeight += 0.5
        if (a.clickable == b.clickable) {
            score += 0.5
        }

        return if (totalWeight > 0.0) score / totalWeight else 0.0
    }

    /**
     * Logs detailed diagnostic information about a target node for debugging.
     */
    fun logNodeDiagnostics(tag: String, nodeId: String, node: AccessibilityNodeInfo?, meta: AccessibilityNode?) {
        if (node == null && meta == null) {
            Log.d(tag, "Target node=$nodeId  [UNRESOLVED - node not found in current tree]")
            return
        }

        val bounds = android.graphics.Rect()
        node?.getBoundsInScreen(bounds)

        val displayMetrics = service.resources.displayMetrics

        Log.d(tag, buildString {
            appendLine("Target node=$nodeId")
            appendLine("  class=${node?.className}")
            appendLine("  text=${node?.text}")
            appendLine("  contentDescription=${node?.contentDescription}")
            appendLine("  clickable=${node?.isClickable}")
            appendLine("  enabled=${node?.isEnabled}")
            appendLine("  visibleToUser=${node?.isVisibleToUser}")
            appendLine("  bounds=[$bounds]")
            appendLine("  display=(${displayMetrics.widthPixels}x${displayMetrics.heightPixels})")
            if (meta != null) {
                append("  meta.bounds=${meta.bounds}")
            }
        })
    }

    fun executeWithNode(
        action: AgentAction,
        resolvedNode: AccessibilityNodeInfo?,
        resolvedNodeMeta: AccessibilityNode?,
        freshNodeMap: Map<String, AccessibilityNodeInfo>
    ): Boolean {
        return when (action.action) {
            "open_app" -> {
                val pkg = action.packageName ?: return false
                val intent = service.packageManager.getLaunchIntentForPackage(pkg)
                if (intent != null) {
                    intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    service.startActivity(intent)
                    true
                } else {
                    false
                }
            }
            "click" -> {
                val node = resolvedNode ?: return false
                clickWithRecovery(node, resolvedNodeMeta, action.nodeId ?: "unknown")
            }
            "long_click" -> {
                val node = resolvedNode ?: return false
                if (node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)) {
                    true
                } else {
                    val parent = getClickableParent(node)
                    if (parent != null) {
                        val parentSuccess = parent.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
                        parent.recycle()
                        if (parentSuccess) return true
                    }
                    false
                }
            }
            "input_text" -> {
                val node = resolvedNode ?: return false
                val text = action.text ?: return false
                val arguments = Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
                }
                if (node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)) {
                    true
                } else {
                    node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
                    node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
                }
            }
            "clear_text" -> {
                val node = resolvedNode ?: return false
                val arguments = Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, "")
                }
                node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
            }
            "scroll" -> {
                val node = resolvedNode ?: return false
                val dir = action.direction ?: "down"
                val act = if (dir == "down" || dir == "right") {
                    AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
                } else {
                    AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
                }
                if (node.performAction(act)) {
                    true
                } else {
                    var current = node.parent
                    var success = false
                    while (current != null) {
                        if (current.isScrollable) {
                            success = current.performAction(act)
                            current.recycle()
                            break
                        }
                        val next = current.parent
                        current.recycle()
                        current = next
                    }
                    success
                }
            }
            "scroll_screen" -> {
                val scrollableNode = findScrollableNode(service.rootInActiveWindow)
                if (scrollableNode != null) {
                    val dir = action.direction ?: "down"
                    val success = if (dir == "down" || dir == "right") {
                        scrollableNode.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)
                    } else {
                        scrollableNode.performAction(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)
                    }
                    scrollableNode.recycle()
                    success
                } else {
                    false
                }
            }
            "back" -> {
                service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
            }
            "home" -> {
                service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
            }
            "open_notifications" -> {
                service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS)
            }
            "open_quick_settings" -> {
                service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_QUICK_SETTINGS)
            }
            "focus" -> {
                val node = resolvedNode ?: return false
                node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            }
            else -> false
        }
    }

    private fun clickWithRecovery(node: AccessibilityNodeInfo, meta: AccessibilityNode?, nodeId: String): Boolean {
        // 1. Try normal click on the node
        val clickResult = node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
        Log.d("AuraActionExecutor", "CLICK node=$nodeId  ACTION_CLICK result=$clickResult")
        if (clickResult) {
            return true
        }

        // 2. Try click on clickable parent
        val parent = getClickableParent(node)
        val parentFound = parent != null
        var parentResult = false
        if (parent != null) {
            parentResult = parent.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            parent.recycle()
        }
        Log.d("AuraActionExecutor", "CLICK node=$nodeId  clickable-parent found=$parentFound  parent-click result=$parentResult")
        if (parentResult) {
            return true
        }

        // 3. Try coordinate-based gesture tap at the center of bounds
        val bounds = meta?.bounds
        if (bounds != null && bounds.size >= 4) {
            val cx = (bounds[0] + bounds[2]) / 2
            val cy = (bounds[1] + bounds[3]) / 2

            val displayMetrics = service.resources.displayMetrics
            val screenW = displayMetrics.widthPixels
            val screenH = displayMetrics.heightPixels

            Log.d("AuraActionExecutor", "CLICK node=$nodeId  gesture coordinates=($cx,$cy)  display=(${screenW}x${screenH})  bounds=$bounds")

            // Validate coordinates are within screen bounds
            if (cx in 1 until screenW && cy in 1 until screenH) {
                val gestureResult = performTapGesture(cx, cy)
                Log.d("AuraActionExecutor", "CLICK node=$nodeId  dispatchGesture result=$gestureResult")
                if (gestureResult.isSuccess) {
                    return true
                }
            } else {
                Log.w("AuraActionExecutor", "CLICK node=$nodeId  gesture coordinates ($cx,$cy) outside screen bounds (${screenW}x${screenH}). Skipping gesture.")
            }
        } else {
            Log.d("AuraActionExecutor", "CLICK node=$nodeId  no valid bounds for gesture fallback. bounds=$bounds")
        }

        Log.e("AuraActionExecutor", "CLICK node=$nodeId  all recovery mechanisms exhausted: ACTION_CLICK=$clickResult, parent=$parentResult, gesture=failed/skipped")
        return false
    }

    private fun getClickableParent(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
        var current = node?.parent
        while (current != null) {
            if (current.isClickable) {
                return current
            }
            val next = current.parent
            current.recycle()
            current = next
        }
        return null
    }

    /**
     * Dispatches a tap gesture at the given screen coordinates.
     *
     * Returns a typed [GestureResult] distinguishing:
     * - DispatchRejected: dispatchGesture() returned false
     * - Completed: callback.onCompleted() fired
     * - Cancelled: callback.onCancelled() fired
     * - Timeout: latch timed out (neither callback fired)
     * - Error: an exception occurred
     */
    fun performTapGesture(x: Int, y: Int): GestureResult {
        if (android.os.Build.VERSION.SDK_INT < android.os.Build.VERSION_CODES.N) {
            Log.w("AuraActionExecutor", "Gesture API not available below API 24.")
            return GestureResult.DispatchRejected
        }

        val path = android.graphics.Path().apply {
            moveTo(x.toFloat(), y.toFloat())
        }
        val gestureDescription = android.accessibilityservice.GestureDescription.Builder()
            .addStroke(android.accessibilityservice.GestureDescription.StrokeDescription(path, 0, 100))
            .build()

        val latch = java.util.concurrent.CountDownLatch(1)
        val resultRef = java.util.concurrent.atomic.AtomicReference<GestureResult>(GestureResult.Timeout)

        val dispatched = try {
            service.dispatchGesture(gestureDescription, object : AccessibilityService.GestureResultCallback() {
                override fun onCompleted(gestureDescription: android.accessibilityservice.GestureDescription?) {
                    resultRef.set(GestureResult.Completed)
                    latch.countDown()
                }
                override fun onCancelled(gestureDescription: android.accessibilityservice.GestureDescription?) {
                    resultRef.set(GestureResult.Cancelled)
                    latch.countDown()
                }
            }, null)
        } catch (e: Exception) {
            Log.e("AuraActionExecutor", "dispatchGesture threw exception: $e")
            return GestureResult.Error(e)
        }

        if (!dispatched) {
            Log.w("AuraActionExecutor", "dispatchGesture returned false — gesture rejected by system at ($x,$y)")
            return GestureResult.DispatchRejected
        }

        try {
            val completed = latch.await(3000, java.util.concurrent.TimeUnit.MILLISECONDS)
            if (!completed) {
                Log.w("AuraActionExecutor", "Gesture callback timed out after 3000ms at ($x,$y)")
                return GestureResult.Timeout
            }
        } catch (e: InterruptedException) {
            Log.e("AuraActionExecutor", "Gesture latch interrupted: $e")
            return GestureResult.Error(e)
        }

        return resultRef.get()

    }

    private fun findScrollableNode(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
        if (node == null) return null
        if (node.isScrollable) return AccessibilityNodeInfo.obtain(node)
        for (i in 0 until node.childCount) {
            val child = node.getChild(i)
            val found = findScrollableNode(child)
            if (found != null) {
                return found
            }
            child?.recycle()
        }
        return null
    }
}
