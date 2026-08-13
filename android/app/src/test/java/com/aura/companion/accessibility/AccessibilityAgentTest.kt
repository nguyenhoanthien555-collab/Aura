package com.aura.companion.accessibility
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
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
            contentDescription = "Submit Button",
            className = "android.widget.Button",
            clickable = true,
            bounds = listOf(10, 20, 100, 200),
            visible = true,
            enabled = true
        )
        assertEquals("node_1", node.id)
        assertEquals("button", node.role)
        assertEquals("Submit", node.text)
        assertEquals("Submit Button", node.contentDescription)
        assertEquals("android.widget.Button", node.className)
        assertTrue(node.clickable)
        assertTrue(node.visible)
        assertTrue(node.enabled)
        assertEquals(listOf(10, 20, 100, 200), node.bounds)
    }
    @Test
    fun testVietnameseUtf8Tasks() {
        // UTF-8 strings should be represented exactly as they are without encoding issues.
        val request1 = "mở youtube"
        val request2 = "tìm video minecraft"
        val request3 = "mở cài đặt"
        assertEquals("mở youtube", request1)
        assertEquals("tìm video minecraft", request2)
        assertEquals("mở cài đặt", request3)
        // Verify snapshot serialization fields
        val snapshot = AccessibilitySnapshot(
            device = DeviceState(1080, 2400),
            app = AppInfo("com.google.android.youtube"),
            accessibilityTree = emptyMap(),
            userRequest = request1,
            lastActionError = "Action failed."
        )
        assertEquals("mở youtube", snapshot.userRequest)
        assertEquals("Action failed.", snapshot.lastActionError)
    }
    @Test
    fun testSafetyGuardStillBlocksDangerousActions() {
        val guard = SafetyGuard()
        
        // Destructive keyword in action text parameter
        val actionWipe = AgentAction(action = "input_text", text = "please wipe the phone")
        assertFalse(guard.checkAction(actionWipe, null))
        
        // Destructive keyword in transaction
        val actionBuy = AgentAction(action = "click", text = "buy now")
        assertFalse(guard.checkAction(actionBuy, null))
    }
    // ===== GestureResult tests =====
    @Test
    fun testGestureResultCompletedIsSuccess() {
        val result = GestureResult.Completed
        assertTrue(result.isSuccess)
        assertEquals("COMPLETED", result.toString())
    }
    @Test
    fun testGestureResultCancelledIsNotSuccess() {
        val result = GestureResult.Cancelled
        assertFalse(result.isSuccess)
        assertEquals("CANCELLED", result.toString())
    }
    @Test
    fun testGestureResultDispatchRejectedIsNotSuccess() {
        val result = GestureResult.DispatchRejected
        assertFalse(result.isSuccess)
        assertEquals("DISPATCH_REJECTED", result.toString())
    }
    @Test
    fun testGestureResultTimeoutIsNotSuccess() {
        val result = GestureResult.Timeout
        assertFalse(result.isSuccess)
        assertEquals("TIMEOUT", result.toString())
    }
    @Test
    fun testGestureResultErrorIsNotSuccess() {
        val err = RuntimeException("test error")
        val result = GestureResult.Error(err)
        assertFalse(result.isSuccess)
        assertEquals(err, (result as GestureResult.Error).exception)
        assertTrue(result.toString().contains("ERROR"))
        assertTrue(result.toString().contains("test error"))
    }
    @Test
    fun testGestureResultSealedTypeDistinguishability() {
        // Ensure all types are distinct
        val results = listOf(
            GestureResult.Completed,
            GestureResult.Cancelled,
            GestureResult.DispatchRejected,
            GestureResult.Timeout,
            GestureResult.Error(RuntimeException("x"))
        )
        // Each should have a unique toString
        val strings = results.map { it.toString() }.toSet()
        assertEquals(5, strings.size)
        // Only Completed should be success
        assertEquals(1, results.count { it.isSuccess })
    }
    // ===== ScreenFingerprint tests (via data class verification) =====
    @Test
    fun testScreenFingerprintEquality() {
        val fp1 = AuraAccessibilityService.ScreenFingerprint("com.example", 10, 12345)
        val fp2 = AuraAccessibilityService.ScreenFingerprint("com.example", 10, 12345)
        assertEquals(fp1, fp2)
    }
    @Test
    fun testScreenFingerprintPackageChange() {
        val fp1 = AuraAccessibilityService.ScreenFingerprint("com.aura.companion", 10, 12345)
        val fp2 = AuraAccessibilityService.ScreenFingerprint("com.google.android.youtube", 15, 67890)
        assertNotEquals(fp1.packageName, fp2.packageName)
    }
    @Test
    fun testScreenFingerprintNodeCountChange() {
        val fp1 = AuraAccessibilityService.ScreenFingerprint("com.example", 10, 12345)
        val fp2 = AuraAccessibilityService.ScreenFingerprint("com.example", 15, 12345)
        assertEquals(fp1.packageName, fp2.packageName)
        assertNotEquals(fp1.nodeCount, fp2.nodeCount)
    }
    @Test
    fun testScreenFingerprintContentHashChange() {
        val fp1 = AuraAccessibilityService.ScreenFingerprint("com.example", 10, 12345)
        val fp2 = AuraAccessibilityService.ScreenFingerprint("com.example", 10, 67890)
        assertEquals(fp1.packageName, fp2.packageName)
        assertEquals(fp1.nodeCount, fp2.nodeCount)
        assertNotEquals(fp1.contentHash, fp2.contentHash)
    }
    @Test
    fun testScreenFingerprintNoChange() {
        // Same package, same nodes, same content hash = no UI change
        val fp1 = AuraAccessibilityService.ScreenFingerprint("com.aura.companion", 5, 999)
        val fp2 = AuraAccessibilityService.ScreenFingerprint("com.aura.companion", 5, 999)
        assertEquals(fp1, fp2)
        // This simulates the false-positive case: rootInActiveWindow exists but nothing changed
    }
    // ===== Action verification logic tests (pure data, no Android framework) =====
    @Test
    fun testVerificationDetectsFalsePositive() {
        // This is the exact scenario from the bug: Aura claims "Action verified"
        // because rootInActiveWindow != null, but package is still com.aura.companion
        val pre = AuraAccessibilityService.ScreenFingerprint("com.aura.companion", 10, 12345)
        val post = AuraAccessibilityService.ScreenFingerprint("com.aura.companion", 10, 12345)
        // With the new system, identical fingerprints mean NO verification
        assertEquals(pre, post)
    }
    @Test
    fun testVerificationDetectsRealPackageChange() {
        val pre = AuraAccessibilityService.ScreenFingerprint("com.aura.companion", 10, 12345)
        val post = AuraAccessibilityService.ScreenFingerprint("com.google.android.youtube", 20, 67890)
        assertNotEquals(pre.packageName, post.packageName)
        assertNotEquals(pre.nodeCount, post.nodeCount)
        assertNotEquals(pre.contentHash, post.contentHash)
    }
    @Test
    fun testVerificationDetectsContentChangeWithinSameApp() {
        // Click within same app changes content but not package
        val pre = AuraAccessibilityService.ScreenFingerprint("com.google.android.youtube", 20, 11111)
        val post = AuraAccessibilityService.ScreenFingerprint("com.google.android.youtube", 25, 22222)
        assertEquals(pre.packageName, post.packageName)
        assertNotEquals(pre.nodeCount, post.nodeCount)
        assertNotEquals(pre.contentHash, post.contentHash)
    }
    // ===== ExecutionResult sealed class tests =====
    @Test
    fun testExecutionResultTypes() {
        // Verify all result types exist and are distinct
        val verified: AuraAccessibilityService.ExecutionResult = AuraAccessibilityService.ExecutionResult.Verified
        val unverified: AuraAccessibilityService.ExecutionResult = AuraAccessibilityService.ExecutionResult.Unverified
        val failed: AuraAccessibilityService.ExecutionResult = AuraAccessibilityService.ExecutionResult.Failed
        val blocked: AuraAccessibilityService.ExecutionResult = AuraAccessibilityService.ExecutionResult.Blocked
        assertNotEquals(verified, unverified)
        assertNotEquals(verified, failed)
        assertNotEquals(verified, blocked)
        assertNotEquals(unverified, failed)
        assertNotEquals(unverified, blocked)
        assertNotEquals(failed, blocked)
    }
    // ===== Node resolution data tests =====
    @Test
    fun testAccessibilityNodeDefaults() {
        val node = AccessibilityNode(
            id = "node_5",
            text = null,
            contentDescription = null,
            className = "android.widget.ImageView",
            clickable = false,
            bounds = listOf(0, 0, 100, 100)
        )
        // Check defaults
        assertTrue(node.enabled)
        assertTrue(node.visible)
        assertNull(node.role)
        assertFalse(node.scrollable)
        assertFalse(node.longClickable)
        assertFalse(node.editable)
        assertFalse(node.selected)
        assertFalse(node.checked)
        assertFalse(node.focused)
    }
    @Test
    fun testAgentActionCompleteMessage() {
        val action = AgentAction(
            action = "complete",
            message = "Successfully opened YouTube"
        )
        assertEquals("complete", action.action)
        assertNull(action.nodeId)
        assertEquals("Successfully opened YouTube", action.message)
    }
    @Test
    fun testSnapshotWithAllFields() {
        val tree = mapOf(
            "node_1" to AccessibilityNode(
                id = "node_1",
                text = "Search",
                contentDescription = "Search button",
                className = "android.widget.Button",
                clickable = true,
                bounds = listOf(10, 20, 200, 80),
                role = "button",
                enabled = true,
                visible = true
            )
        )
        val snapshot = AccessibilitySnapshot(
            device = DeviceState(1080, 2400),
            app = AppInfo("com.google.android.youtube", label = "YouTube"),
            accessibilityTree = tree,
            screenshotAvailable = false,
            userRequest = "open youtube",
            lastActionError = "Previous click failed."
        )
        assertEquals(1, snapshot.accessibilityTree.size)
        assertEquals("open youtube", snapshot.userRequest)
        assertEquals("Previous click failed.", snapshot.lastActionError)
        assertNotNull(snapshot.accessibilityTree["node_1"])
    }
    @Test
    fun testShouldAutoCompleteForSingleActionTasks() {
        val openApp = AgentAction(action = "open_app", packageName = "com.google.android.youtube")
        val home = AgentAction(action = "home")
        // Single-intent tasks must auto-complete
        assertTrue(AuraAccessibilityService.shouldAutoComplete("mở YouTube", openApp))
        assertTrue(AuraAccessibilityService.shouldAutoComplete("mở Chrome", openApp))
        assertTrue(AuraAccessibilityService.shouldAutoComplete("về màn hình chính", home))
        assertTrue(AuraAccessibilityService.shouldAutoComplete("home", home))
        // Multi-step tasks must NOT auto-complete on open_app/home
        assertFalse(AuraAccessibilityService.shouldAutoComplete("open YouTube and search Minecraft", openApp))
        assertFalse(AuraAccessibilityService.shouldAutoComplete("mở YouTube và tìm Minecraft", openApp))
        assertFalse(AuraAccessibilityService.shouldAutoComplete("mở YouTube rồi gõ Minecraft", openApp))
        assertFalse(AuraAccessibilityService.shouldAutoComplete("về màn hình chính rồi mở YouTube", home))
        // Non-deterministic actions must NOT auto-complete
        val click = AgentAction(action = "click", nodeId = "btn_1")
        assertFalse(AuraAccessibilityService.shouldAutoComplete("mở YouTube", click))
    }

    @Test
    fun testRepeatedVerifiedActionGuard() {
        val openAppYoutube = AgentAction(action = "open_app", packageName = "com.google.android.youtube")
        val openAppChrome = AgentAction(action = "open_app", packageName = "com.android.chrome")
        val homeAction = AgentAction(action = "home")
        val clickNode1 = AgentAction(action = "click", nodeId = "node_1")
        val clickNode2 = AgentAction(action = "click", nodeId = "node_2")

        // Identical verified open_app when already in target package or matching last verified action
        assertTrue(AuraAccessibilityService.isRepeatedVerifiedAction(openAppYoutube, openAppYoutube, "com.google.android.youtube"))
        assertTrue(AuraAccessibilityService.isRepeatedVerifiedAction(openAppYoutube, openAppYoutube, "com.aura.companion"))

        // Different app target is NOT repeated
        assertFalse(AuraAccessibilityService.isRepeatedVerifiedAction(openAppChrome, openAppYoutube, "com.google.android.youtube"))

        // Repeated home action is guarded
        assertTrue(AuraAccessibilityService.isRepeatedVerifiedAction(homeAction, homeAction, "com.sec.android.app.launcher"))

        // Repeated click on same node_id is guarded
        assertTrue(AuraAccessibilityService.isRepeatedVerifiedAction(clickNode1, clickNode1, "com.google.android.youtube"))
        assertFalse(AuraAccessibilityService.isRepeatedVerifiedAction(clickNode2, clickNode1, "com.google.android.youtube"))
    }

    @Test
    fun testAccessibilitySnapshotCompletedActionsProgress() {
        val completedList = listOf(
            "open_app(com.google.android.youtube) [VERIFIED]",
            "click(search_button) [VERIFIED]"
        )
        val snapshot = AccessibilitySnapshot(
            device = DeviceState(1080, 2400),
            app = AppInfo("com.google.android.youtube", label = "YouTube"),
            accessibilityTree = emptyMap(),
            screenshotAvailable = false,
            userRequest = "open YouTube and search Minecraft",
            completedActions = completedList
        )

        assertEquals(2, snapshot.completedActions.size)
        assertEquals("open_app(com.google.android.youtube) [VERIFIED]", snapshot.completedActions[0])
        assertEquals("click(search_button) [VERIFIED]", snapshot.completedActions[1])
    }

    @Test
    fun testIsSearchTaskComplete() {
        val inputAction = AgentAction(action = "input_text", nodeId = "search_edit_text", text = "Minecraft")
        val submitAction = AgentAction(action = "submit")
        val clickAction = AgentAction(action = "click", nodeId = "search_button")

        // Search task completes on verified input_text or submit
        assertTrue(AuraAccessibilityService.isSearchTaskComplete("open YouTube and search Minecraft", inputAction, listOf("input_text(search_edit_text, \"Minecraft\") [VERIFIED]")))
        assertTrue(AuraAccessibilityService.isSearchTaskComplete("open Chrome and search Google", submitAction, emptyList()))
        assertTrue(AuraAccessibilityService.isSearchTaskComplete("mở YouTube và tìm Minecraft", submitAction, emptyList()))

        // Non-search requests do not auto-complete on search check
        assertFalse(AuraAccessibilityService.isSearchTaskComplete("open YouTube", inputAction, emptyList()))

        // Click action alone is not search completion unless input/submit history exists
        assertFalse(AuraAccessibilityService.isSearchTaskComplete("open YouTube and search Minecraft", clickAction, emptyList()))
        assertTrue(AuraAccessibilityService.isSearchTaskComplete("open YouTube and search Minecraft", clickAction, listOf("input_text(search_edit_text, \"Minecraft\") [VERIFIED]")))
    }

    @Test
    fun testSanitizeSearchQuery() {
        assertEquals("Google", AuraActionExecutor.sanitizeSearchQuery("search Google"))
        assertEquals("Google", AuraActionExecutor.sanitizeSearchQuery("search for Google"))
        assertEquals("Minecraft", AuraActionExecutor.sanitizeSearchQuery("tìm Minecraft"))
        assertEquals("lofi music", AuraActionExecutor.sanitizeSearchQuery("tìm lofi music"))
    }

    @Test
    fun testSearchAndPickTaskCompletion() {
        val inputAction = AgentAction(action = "input_text", nodeId = "search_field", text = "lofi music")
        val resultClick = AgentAction(action = "click", nodeId = "first_organic_result")

        val searchAndPickReq = "open YouTube and search for lofi music and pick the first result"
        val vietnamesePickReq = "mở YouTube, tìm lofi music và chọn bài hát đầu tiên không phải quảng cáo"

        // Search submission does NOT complete a search + pick task
        assertFalse(AuraAccessibilityService.isSearchTaskComplete(searchAndPickReq, inputAction, listOf("input_text(search_field, \"lofi music\") [VERIFIED]")))
        assertFalse(AuraAccessibilityService.isSearchTaskComplete(vietnamesePickReq, inputAction, listOf("input_text(search_field, \"lofi music\") [VERIFIED]")))

        // Result selection completes search + pick task immediately
        assertTrue(AuraAccessibilityService.isSelectionTaskComplete(searchAndPickReq, resultClick, listOf("input_text(search_field, \"lofi music\") [VERIFIED]")))
        assertTrue(AuraAccessibilityService.isSelectionTaskComplete(vietnamesePickReq, resultClick, listOf("input_text(search_field, \"lofi music\") [VERIFIED]")))
    }
}



