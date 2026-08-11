package com.aura.companion.accessibility

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * Decides whether a typed message wants something *done* or something
 * *said*.
 *
 * Previously there was no decision: whenever the accessibility service
 * was running, every message went into the agent loop. "hey how are you"
 * became a screen capture, a snapshot upload and up to ten silent steps
 * ending in a canned completion, and the assistant stopped being able to
 * hold a conversation on the one device where it could also act
 * (AURA-P1-004).
 *
 * The judgement is made by the model, not by a keyword table here. A
 * table would have to be right about "mở youtube", "put on some music",
 * "can you get me into settings" and "what's the weather" - four
 * sentences with no shared surface feature - and would still be wrong in
 * the next language somebody types. The server builds a small prompt for
 * exactly this question (see brain/prompt_builder._build_intent_prompt)
 * and answers in one word.
 *
 * The two mistakes do not cost the same, so this is deliberately biased.
 * Treating a conversation as an action wastes a screen capture and up to
 * ten steps on a message that only wanted an answer; treating an action
 * as a conversation costs one reply and the user can rephrase. Anything
 * unclear is therefore conversation.
 */
object IntentRouter {

    const val ACTION = "action"

    const val CONVERSATION = "conversation"

    /**
     * Context marking a request as a routing question rather than a turn
     * of conversation.
     *
     * The server reads this key - never the message text - to decide the
     * reply is for a parser: not styled, not saved to the transcript,
     * and not announced to any UI. See brain/agent_mode.py.
     */
    const val INTENT_PROBE_KEY = "intent_probe"

    val PROBE_CONTEXT: JsonObject = JsonObject(
        mapOf(INTENT_PROBE_KEY to JsonPrimitive(true))
    )

    /**
     * True only when the reply says "action" and nothing else.
     *
     * The server normalises its answer to one of the two words, so in
     * practice this is a comparison. It stays tolerant anyway - matching
     * on words rather than on the whole string, and refusing a reply that
     * contains both labels - because an app in the field can be talking
     * to an older server, and the failure it would otherwise produce is
     * an unexplained agent loop rather than a visible error.
     */
    fun isAction(reply: String?): Boolean {

        val words = WORD.findAll((reply ?: "").lowercase())
            .map { it.value }
            .toSet()

        return ACTION in words && CONVERSATION !in words
    }

    private val WORD = Regex("[a-z]+")
}
