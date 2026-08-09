package com.aura.companion.accessibility

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo

object AccessibilityNodeSerializer {

    fun serialize(
        root: AccessibilityNodeInfo,
        nodeMap: MutableMap<String, AccessibilityNodeInfo>
    ): Map<String, AccessibilityNode> {
        val serializedNodes = mutableMapOf<String, AccessibilityNode>()
        var nodeCounter = 1

        fun walk(node: AccessibilityNodeInfo?) {
            if (node == null) return

            // Password fields are skipped for safety
            if (node.isPassword) return

            val isImportant = node.text != null ||
                              node.contentDescription != null ||
                              node.isClickable ||
                              node.isScrollable ||
                              node.isEditable ||
                              node.isFocusable

            if (isImportant && node.isVisibleToUser) {
                val id = "node_$nodeCounter"
                nodeCounter++

                // Store in mapping for execution lookup
                nodeMap[id] = AccessibilityNodeInfo.obtain(node)

                val bounds = Rect()
                node.getBoundsInScreen(bounds)

                val role = node.className?.toString()?.substringAfterLast('.').orEmpty()

                serializedNodes[id] = AccessibilityNode(
                    id = id,
                    role = if (role.isBlank()) "view" else role.lowercase(),
                    className = node.className?.toString(),
                    text = node.text?.toString(),
                    contentDescription = node.contentDescription?.toString(),
                    clickable = node.isClickable,
                    bounds = listOf(bounds.left, bounds.top, bounds.right, bounds.bottom),
                    scrollable = node.isScrollable,
                    enabled = node.isEnabled,
                    visible = node.isVisibleToUser,
                    longClickable = node.isLongClickable,
                    editable = node.isEditable,
                    selected = node.isSelected,
                    checked = node.isChecked,
                    focused = node.isFocused
                )
            }

            for (i in 0 until node.childCount) {
                val child = node.getChild(i)
                walk(child)
            }
        }

        walk(root)
        return serializedNodes
    }
}
