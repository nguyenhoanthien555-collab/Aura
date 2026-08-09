package com.aura.companion.accessibility

import android.view.accessibility.AccessibilityNodeInfo

class SafetyGuard {

    companion object {
        val DANGEROUS_PACKAGES = setOf(
            "com.android.settings",
            "com.google.android.settings",
            "com.android.providers.settings",
            "com.android.vending", // Play Store purchases
            "com.google.android.apps.docs" // Google Drive file deletion
        )

        val DANGEROUS_KEYWORDS = setOf(
            "reset", "wipe", "delete", "uninstall", "clear data", "purchase", "buy", "pay",
            "transaction", "disabled", "disable", "grant", "permission", "admin", "factory reset",
            "erase", "format"
        )
    }

    /**
     * Checks if the action is safe to execute automatically.
     * Returns true if safe, false if it requires explicit user confirmation.
     */
    fun checkAction(action: AgentAction, node: AccessibilityNodeInfo?): Boolean {
        // 1. Open app action check
        if (action.action == "open_app") {
            val pkg = action.packageName.orEmpty()
            if (DANGEROUS_PACKAGES.contains(pkg) || pkg.contains("settings") || pkg.contains("security")) {
                return false
            }
        }

        // 2. Node text/description/role checks
        if (node != null) {
            val packageName = node.packageName?.toString().orEmpty()
            if (DANGEROUS_PACKAGES.contains(packageName)) {
                return false
            }

            val text = node.text?.toString()?.lowercase().orEmpty()
            val desc = node.contentDescription?.toString()?.lowercase().orEmpty()

            for (keyword in DANGEROUS_KEYWORDS) {
                if (text.contains(keyword) || desc.contains(keyword)) {
                    return false
                }
            }
        }

        // 3. Action parameter checks (e.g. inputting text that might be destructive)
        val textParam = action.text?.lowercase().orEmpty()
        for (keyword in DANGEROUS_KEYWORDS) {
            if (textParam.contains(keyword)) {
                return false
            }
        }

        return true
    }
}
