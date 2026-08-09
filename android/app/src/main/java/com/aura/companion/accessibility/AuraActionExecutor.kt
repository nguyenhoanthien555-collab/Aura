package com.aura.companion.accessibility

import android.accessibilityservice.AccessibilityService
import android.content.Intent
import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo

class AuraActionExecutor(
    private val service: AccessibilityService
) {

    fun execute(action: AgentAction, nodeMap: Map<String, AccessibilityNodeInfo>): Boolean {
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
                val node = nodeMap[action.nodeId] ?: return false
                node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            }
            "long_click" -> {
                val node = nodeMap[action.nodeId] ?: return false
                node.performAction(AccessibilityNodeInfo.ACTION_LONG_CLICK)
            }
            "input_text" -> {
                val node = nodeMap[action.nodeId] ?: return false
                val text = action.text ?: return false
                val arguments = Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
                }
                node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
            }
            "clear_text" -> {
                val node = nodeMap[action.nodeId] ?: return false
                val arguments = Bundle().apply {
                    putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, "")
                }
                node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
            }
            "scroll" -> {
                val node = nodeMap[action.nodeId] ?: return false
                val dir = action.direction ?: "down"
                if (dir == "down" || dir == "right") {
                    node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_FORWARD)
                } else {
                    node.performAction(AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD)
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
                val node = nodeMap[action.nodeId] ?: return false
                node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
            }
            else -> false
        }
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
        }
        return null
    }
}
