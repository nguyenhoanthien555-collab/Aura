package com.aura.companion.accessibility

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * What identifies one action, on the phone and on the wire.
 *
 * Three things have to agree about that, and for a while they did not.
 * The retry guard counts failures per action, the history formatter tells
 * the server what was done, and `brain/agent_mode.py` reads both back into
 * `(kind, target)` records. When the guard's notion of "same action"
 * differed from the formatter's, the count that refused to execute and the
 * count the server was told about were about different things.
 *
 * The concrete defect these pin: `actionKey` read `action.nodeId` for
 * every kind, and a launch carries no node id - so every launch of every
 * app keyed to the literal string "open_app:null". Two failed attempts at
 * one app and the next launch, of any app at all, was refused before it
 * was tried. Nothing in the message said so; it surfaced as Aura simply
 * declining to open things.
 */
class ActionIdentityTest {

    private val youtube = AgentAction(action = "open_app", packageName = "com.google.android.youtube")
    private val chrome = AgentAction(action = "open_app", packageName = "com.android.chrome")

    @Test
    fun aLaunchIsIdentifiedByItsPackage() {
        assertEquals("com.google.android.youtube", AuraAccessibilityService.actionTarget(youtube))
        assertEquals("open_app:com.google.android.youtube", AuraAccessibilityService.actionKey(youtube))
    }

    @Test
    fun twoAppsAreNeverTheSameAction() {
        // The regression itself. Both used to key to "open_app:null".
        assertNotEquals(
            AuraAccessibilityService.actionKey(youtube),
            AuraAccessibilityService.actionKey(chrome),
        )
    }

    @Test
    fun oneAppsFailuresDoNotSpendAnothersAttempts() {
        // The guard's arithmetic, run over the same map it uses.
        val failures = mutableMapOf<String, Int>()
        failures[AuraAccessibilityService.actionKey(chrome)] = AuraAccessibilityService.MAX_ACTION_ATTEMPTS

        val chromeRefused =
            (failures[AuraAccessibilityService.actionKey(chrome)] ?: 0) >= AuraAccessibilityService.MAX_ACTION_ATTEMPTS
        val youtubeRefused =
            (failures[AuraAccessibilityService.actionKey(youtube)] ?: 0) >= AuraAccessibilityService.MAX_ACTION_ATTEMPTS

        assertTrue(chromeRefused)
        // YouTube has never been tried, so its first attempt must happen.
        assertEquals(false, youtubeRefused)
    }

    @Test
    fun anActionOnNoParticularTargetHasNoTarget() {
        for (kind in listOf("home", "back", "submit", "open_notifications", "open_quick_settings")) {
            val action = AgentAction(action = kind, nodeId = "node_7")

            // A node id sent alongside one of these is noise: the gesture
            // does not act on a node, so keying by one would make the same
            // global gesture look like a different action each tick, and
            // the bound would never be reached.
            assertEquals("", AuraAccessibilityService.actionTarget(action))
            assertEquals("$kind:", AuraAccessibilityService.actionKey(action))
        }
    }

    @Test
    fun anActionOnANodeIsIdentifiedByThatNode() {
        for (kind in listOf("click", "long_click", "focus", "scroll", "clear_text")) {
            val action = AgentAction(action = kind, nodeId = "node_12")

            assertEquals("node_12", AuraAccessibilityService.actionTarget(action))
            assertEquals("$kind:node_12", AuraAccessibilityService.actionKey(action))
        }
    }

    @Test
    fun everySignatureNamesTheSameTargetItsKeyDoes() {
        val actions = listOf(
            youtube,
            chrome,
            AgentAction(action = "click", nodeId = "node_3"),
            AgentAction(action = "input_text", nodeId = "node_4", text = "Minecraft"),
            AgentAction(action = "scroll", nodeId = "node_5", direction = "down"),
            AgentAction(action = "submit"),
            AgentAction(action = "home"),
        )

        for (action in actions) {
            // The pin that stops the two from drifting. A future kind added
            // to `actionTarget` gets both behaviours or neither; a signature
            // built from something else would fail here rather than quietly
            // give the server a second record for one action.
            val expected = "${action.action}(${AuraAccessibilityService.actionTarget(action)}"

            assertTrue(
                "signature ${AuraAccessibilityService.actionSignature(action)} does not start with $expected",
                AuraAccessibilityService.actionSignature(action).startsWith(expected),
            )
        }
    }

    @Test
    fun typedTextTravelsWithTheActionButNotInItsIdentity() {
        val typing = AgentAction(action = "input_text", nodeId = "search_box", text = "Minecraft")

        // The text is in the line, because the model benefits from seeing
        // what was already typed...
        assertEquals(
            "input_text(search_box, \"Minecraft\")",
            AuraAccessibilityService.actionSignature(typing),
        )

        // ...and not in the key, because a retry with corrected text is
        // still the same step of the task. Two records would mean the bound
        // on neither was ever reached.
        assertEquals("input_text:search_box", AuraAccessibilityService.actionKey(typing))
        assertEquals(
            AuraAccessibilityService.actionKey(typing),
            AuraAccessibilityService.actionKey(typing.copy(text = "Minecrafr")),
        )
    }

    @Test
    fun aFailureReportsItsVerdictAndHowManyTimes() {
        assertEquals(
            "open_app(com.android.chrome) [FAILED x2]",
            AuraAccessibilityService.formatActionFailure(chrome, "FAILED", 2),
        )
        assertEquals(
            "click(node_9) [UNVERIFIED x1]",
            AuraAccessibilityService.formatActionFailure(
                AgentAction(action = "click", nodeId = "node_9"),
                "UNVERIFIED",
                1,
            ),
        )
    }

    @Test
    fun successAndFailureDescribeOneActionTwoWays() {
        val typing = AgentAction(action = "input_text", nodeId = "search_box", text = "lofi music")

        // Same prefix, different verdict. This is what lets the server key
        // both channels into one record: `read_action_history` and
        // `read_action_failures` share `_target_of`, and here the two
        // formatters share `actionSignature`.
        assertEquals(
            "input_text(search_box, \"lofi music\") [VERIFIED]",
            AuraAccessibilityService.formatActionHistory(typing),
        )
        assertEquals(
            "input_text(search_box, \"lofi music\") [FAILED x2]",
            AuraAccessibilityService.formatActionFailure(typing, "FAILED", 2),
        )
    }

    @Test
    fun theSnapshotCarriesFailuresAndDefaultsToNone() {
        val reported = AccessibilitySnapshot(
            device = DeviceState(1080, 2400),
            app = AppInfo("com.android.chrome", label = "Chrome"),
            accessibilityTree = emptyMap(),
            userRequest = "open YouTube and search Minecraft",
            completedActions = listOf("home() [VERIFIED]"),
            failedActions = listOf("open_app(com.google.android.youtube) [FAILED x2]"),
        )

        assertEquals(1, reported.failedActions.size)
        assertEquals("open_app(com.google.android.youtube) [FAILED x2]", reported.failedActions[0])

        // Defaulted, so a build predating the field still deserialises and
        // the server reads silence as "nothing reported" rather than as an
        // error. `test_recovery.py` asserts the same property server-side.
        val quiet = AccessibilitySnapshot(
            device = DeviceState(1080, 2400),
            app = AppInfo("com.android.chrome", label = "Chrome"),
            accessibilityTree = emptyMap(),
        )

        assertTrue(quiet.failedActions.isEmpty())
    }

    @Test
    fun theServiceNeverAsksForMoreAttemptsThanItWillPerform() {
        // The floor the server's DEFAULT_RETRY_LIMIT is pinned against by
        // tests/test_agent_protocol.py. Above one, because a limit of one
        // would refuse every retry; small, because each attempt is a real
        // gesture on a real screen.
        assertTrue(AuraAccessibilityService.MAX_ACTION_ATTEMPTS >= 2)
        assertTrue(AuraAccessibilityService.MAX_ACTION_ATTEMPTS <= 5)
    }
}
