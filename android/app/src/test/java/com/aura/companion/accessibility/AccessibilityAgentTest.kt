package com.aura.companion.accessibility

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AccessibilityAgentTest {

    @Test
    fun testSafetyGuardOpenApp() {
        val guard = SafetyGuard()

        // Settings app should be blocked
        val actionSettings = AgentAction(action = "open_app", packageName = "com.android.settings")
        assertFalse(guard.checkAction(actionSettings, null))

        // Normal app should be allowed
        val actionDiscord = AgentAction(action = "open_app", packageName = "com.discord")
        assertTrue(guard.checkAction(actionDiscord, null))
    }

    @Test
    fun testSafetyGuardKeywords() {
        val guard = SafetyGuard()

        // Text input with dangerous words should be blocked
        val actionReset = AgentAction(action = "input_text", text = "factory reset my phone")
        assertFalse(guard.checkAction(actionReset, null))

        // Normal text input should be allowed
        val actionHello = AgentAction(action = "input_text", text = "hello there")
        assertTrue(guard.checkAction(actionHello, null))
    }

    @Test
    fun testActionExecutorValidation() {
        val action = AgentAction(
            action = "click",
            nodeId = "node_1",
            text = "Search",
            direction = "down",
            packageName = "com.example"
        )
        assertEquals("click", action.action)
        assertEquals("node_1", action.nodeId)
        assertEquals("Search", action.text)
        assertEquals("down", action.direction)
        assertEquals("com.example", action.packageName)
    }

    @Test
    fun testAccessibilityNodeModel() {
        val node = AccessibilityNode(
            id = "node_1",
            role = "button",
            text = "Submit",
            description = "Submit Button",
            clickable = true,
            bounds = listOf(10, 20, 100, 200)
        )
        assertEquals("node_1", node.id)
        assertEquals("button", node.role)
        assertEquals("Submit", node.text)
        assertEquals("Submit Button", node.description)
        assertTrue(node.clickable)
        assertEquals(listOf(10, 20, 100, 200), node.bounds)
    }
}
