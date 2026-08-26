package com.aura.companion.accessibility

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Reading a model's reply, and deciding whether it did anything.
 *
 * Three defects meet here. The parser rejected any reply that was not
 * bare JSON and the loop broke outright on rejection, so a fenced or
 * prose-wrapped answer ended a task the model had answered correctly
 * (AURA-P0-008). The `open_app` check passed on *any* package change, so
 * a permission dialog counted as the app opening (AURA-P1-003). And
 * every typed message went into the agent loop whenever the service was
 * running, so "hey how are you" became ten silent steps (AURA-P1-004).
 *
 * These are pure-JVM: the parser, the router and the verification rule
 * are deliberately free of Android types so the decisions that let Aura
 * claim success can be tested directly.
 */
class AgentActionParserTest {

    private fun parsed(reply: String): AgentAction {
        val result = AgentActionParser.parse(reply)
        assertTrue("expected a parse, got $result", result is AgentActionParser.ParseResult.Success)
        return (result as AgentActionParser.ParseResult.Success).action
    }

    private fun failure(reply: String?): String {
        val result = AgentActionParser.parse(reply)
        assertTrue("expected a failure, got $result", result is AgentActionParser.ParseResult.Failure)
        return (result as AgentActionParser.ParseResult.Failure).reason
    }

    // ------------------------------------------------------------------
    // What must parse
    // ------------------------------------------------------------------

    @Test
    fun bareJsonParses() {
        val action = parsed("""{"action":"click","node_id":"node_1"}""")
        assertEquals("click", action.action)
        assertEquals("node_1", action.nodeId)
    }

    @Test
    fun fencedJsonParses() {
        val action = parsed(
            """
            ```json
            {"action":"open_app","package":"com.google.android.youtube"}
            ```
            """.trimIndent()
        )
        assertEquals("open_app", action.action)
        assertEquals("com.google.android.youtube", action.packageName)
    }

    @Test
    fun fenceWithoutLanguageTagParses() {
        val action = parsed("```\n{\"action\":\"back\"}\n```")
        assertEquals("back", action.action)
    }

    @Test
    fun proseBeforeAndAfterTheObjectIsIgnored() {
        val action = parsed(
            "Sure! Here's the next action:\n" +
                "{\"action\":\"scroll\",\"direction\":\"down\"}\n" +
                "Let me know if that works."
        )
        assertEquals("scroll", action.action)
        assertEquals("down", action.direction)
    }

    @Test
    fun unknownFieldsAreIgnored() {
        // A model that adds "reasoning" or "confidence" has still named
        // an action. Rejecting the whole reply over a field we do not
        // read is the brittleness this parser exists to remove.
        val action = parsed(
            """{"action":"click","node_id":"n2","reasoning":"the search box","confidence":0.9}"""
        )
        assertEquals("click", action.action)
        assertEquals("n2", action.nodeId)
    }

    @Test
    fun aSingleActionWrappedInAnArrayIsRead() {
        val action = parsed("""[{"action":"home"}]""")
        assertEquals("home", action.action)
    }

    @Test
    fun bracesInsideAStringDoNotEndTheObjectEarly() {
        val action = parsed("""{"action":"input_text","text":"use {} for a dict"}""")
        assertEquals("input_text", action.action)
        assertEquals("use {} for a dict", action.text)
    }

    @Test
    fun anEscapedQuoteDoesNotCloseTheString() {
        val action = parsed("""{"action":"input_text","text":"say \"hi\" back"}""")
        assertEquals("""say "hi" back""", action.text)
    }

    @Test
    fun nestedObjectsAreMatchedToTheOuterBrace() {
        val action = parsed("""{"action":"click","extra":{"a":{"b":1}},"node_id":"n7"}""")
        assertEquals("click", action.action)
        assertEquals("n7", action.nodeId)
    }

    @Test
    fun surroundingWhitespaceInTheActionNameIsNormalised() {
        // Otherwise a stray space misses the executor's `when` and gets
        // reported to the user as an unactionable target.
        assertEquals("click", parsed("""{"action":" click "}""").action)
    }

    @Test
    fun vietnameseCompletionMessageSurvivesIntact() {
        val action = parsed("""{"action":"complete","message":"Đã mở YouTube rồi nè~"}""")
        assertEquals("complete", action.action)
        assertEquals("Đã mở YouTube rồi nè~", action.message)
    }

    @Test
    fun everySupportedActionNameRoundTripsThroughTheParser() {
        // Every name in KNOWN_ACTIONS must actually survive parsing -
        // membership in a set is not the same as being accepted, since
        // normalisation happens first.
        //
        // This does NOT guard against the AGENT RULES prompt drifting,
        // despite what it used to claim: the list below and
        // KNOWN_ACTIONS are both Kotlin, and neither reads the prompt. It
        // stayed green through the whole `submit` defect for exactly that
        // reason - the prompt offered submit, the executor implemented
        // it, and this test compared a hardcoded copy against the
        // constant it was copied from. The real guard has to cross the
        // language boundary and lives in Python:
        // test_the_parser_accepts_every_action_the_prompt_offers in
        // tests/test_agent_protocol.py.
        val supported = listOf(
            "complete", "click", "long_click", "input_text", "clear_text",
            "scroll", "scroll_screen", "back", "home", "open_notifications",
            "open_quick_settings", "open_app", "focus", "submit",
        )
        for (name in supported) {
            assertEquals(name, parsed("""{"action":"$name"}""").action)
        }
        assertEquals(supported.toSet(), AgentActionParser.KNOWN_ACTIONS)
    }

    // ------------------------------------------------------------------
    // What must not parse, and what the model is told about it
    // ------------------------------------------------------------------

    @Test
    fun plainProseIsAFailure() {
        val reason = failure("I'm not sure which button you mean.")
        assertTrue(reason.contains("JSON"))
    }

    @Test
    fun truncatedJsonIsAFailure() {
        // Most likely a token limit. Half an object is not half an
        // action, so nothing is executed.
        failure("""{"action":"input_text","text":"hello""")
    }

    @Test
    fun emptyAndNullRepliesAreFailures() {
        failure("")
        failure("   ")
        failure(null)
    }

    @Test
    fun anObjectWithoutAnActionFieldIsAFailure() {
        failure("""{"node_id":"node_1"}""")
    }

    @Test
    fun anUnknownActionNameIsRefusedAndNamedBackToTheModel() {
        // Executing it would fail anyway; saying precisely what was
        // wrong gives the model something it can act on instead of a
        // generic "target not clickable".
        val reason = failure("""{"action":"teleport","node_id":"n1"}""")
        assertTrue(reason.contains("teleport"))
        assertTrue(reason.contains("click"))
    }

    @Test
    fun aFailureReasonIsAddressedToTheModel() {
        // It travels back as `last_action_error`, which is the only
        // channel the model has to correct itself.
        val reason = failure("no json here")
        assertTrue(reason.contains("Reply with"))
    }

    // ------------------------------------------------------------------
    // extractJsonObject directly
    // ------------------------------------------------------------------

    @Test
    fun extractionReturnsOnlyTheObject() {
        assertEquals(
            """{"action":"home"}""",
            AgentActionParser.extractJsonObject("""before {"action":"home"} after {oops""")
        )
    }

    @Test
    fun extractionRefusesAnUnbalancedObject() {
        assertNull(AgentActionParser.extractJsonObject("""{"action":"home" """))
        assertNull(AgentActionParser.extractJsonObject("no braces at all"))
        assertNull(AgentActionParser.extractJsonObject(null))
    }
}

/**
 * `open_app` is only verified by arriving at the app that was asked for.
 */
class OpenAppVerificationTest {

    private fun screen(pkg: String) =
        AuraAccessibilityService.ScreenFingerprint(
            packageName = pkg,
            nodeCount = 10,
            contentHash = 1234,
        )

    @Test
    fun reachingTheRequestedPackageVerifies() {
        assertTrue(
            AuraAccessibilityService.verifyOpenApp(
                target = "com.google.android.youtube",
                pre = screen("com.android.launcher"),
                post = screen("com.google.android.youtube"),
            )
        )
    }

    @Test
    fun landingSomewhereElseDoesNotVerify() {
        // The defect. Under the old rule any package change passed, so a
        // permission dialog, a chooser sheet, a launcher redirect or a
        // crash back to the home screen all counted as "YouTube is open"
        // and the agent reported success for an app never opened.
        for (elsewhere in listOf(
            "com.google.android.permissioncontroller",
            "android",
            "com.android.launcher",
            "com.google.android.youtube.other",
        )) {
            assertFalse(
                "expected $elsewhere not to verify",
                AuraAccessibilityService.verifyOpenApp(
                    target = "com.google.android.youtube",
                    pre = screen("com.aura.companion"),
                    post = screen(elsewhere),
                )
            )
        }
    }

    @Test
    fun alreadyBeingInTheRequestedAppVerifies() {
        // Nothing changed, and nothing needed to: the requested app is
        // in the foreground, which is the whole claim being made.
        assertTrue(
            AuraAccessibilityService.verifyOpenApp(
                target = "com.google.android.youtube",
                pre = screen("com.google.android.youtube"),
                post = screen("com.google.android.youtube"),
            )
        )
    }

    @Test
    fun withNoPackageNamedAChangeIsTheBestEvidenceThereIs() {
        assertTrue(
            AuraAccessibilityService.verifyOpenApp(
                target = "",
                pre = screen("com.aura.companion"),
                post = screen("com.google.android.youtube"),
            )
        )
        assertFalse(
            AuraAccessibilityService.verifyOpenApp(
                target = "",
                pre = screen("com.aura.companion"),
                post = screen("com.aura.companion"),
            )
        )
    }
}

/**
 * Conversation stays conversation.
 */
class IntentRouterTest {

    @Test
    fun onlyAClearActionReplyEntersTheAgentLoop() {
        assertTrue(IntentRouter.isAction("action"))
        assertTrue(IntentRouter.isAction("ACTION"))
        assertTrue(IntentRouter.isAction(" action \n"))
        assertTrue(IntentRouter.isAction("Action."))
    }

    @Test
    fun anythingUnclearStaysConversation() {
        // The two mistakes do not cost the same: a misrouted
        // conversation spends a screen capture and up to ten silent
        // steps, a misrouted action costs one sentence.
        for (reply in listOf(
            "conversation",
            "",
            "   ",
            null,
            "this is an action, or maybe conversation",
            "unsure",
            "actionable",
        )) {
            assertFalse("expected conversation for '$reply'", IntentRouter.isAction(reply))
        }
    }

    @Test
    fun theProbeContextCarriesTheFlagTheServerReads() {
        // The server decides a turn is a probe from this key, never from
        // the message text, so the key has to be exactly this.
        assertEquals(
            "intent_probe",
            IntentRouter.INTENT_PROBE_KEY
        )
        assertEquals("true", IntentRouter.PROBE_CONTEXT["intent_probe"]?.toString())
    }
}
