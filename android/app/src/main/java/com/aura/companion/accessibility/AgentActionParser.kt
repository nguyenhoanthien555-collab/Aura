package com.aura.companion.accessibility

import kotlinx.serialization.json.Json

/**
 * Reads an [AgentAction] out of whatever the model actually replied.
 *
 * The prompt asks for a raw JSON object and nothing else. Models comply
 * most of the time and then, unpredictably, do not: they wrap the object
 * in a ```json fence, prefix it with "Sure! Here's the next action:",
 * add a field the app has never heard of, or answer with a one-element
 * array. None of that changes what the model decided to do, so none of
 * it should end the task.
 *
 * The previous parser used `Json.decodeFromString` with the default
 * configuration, which rejects every one of those, and a rejection broke
 * the loop outright with "Failed to parse action from server" - ending a
 * request the model had answered correctly.
 *
 * What is deliberately *not* tolerated: a reply with no JSON object in
 * it, an object with no `action`, and an action this app cannot execute.
 * Those are real disagreements about the contract, and each returns a
 * [Failure] whose reason is written to be sent back to the model as
 * `last_action_error` - which is the only channel it has to correct
 * itself.
 */
object AgentActionParser {

    /**
     * Every action the executor can carry out, plus the terminal
     * `complete`.
     *
     * Kept in step with the supported set listed in the AGENT RULES
     * prompt section (see brain/prompt_builder.py). An action outside
     * this set is a hallucination: executing it would fail anyway, and
     * saying so precisely here gives the model something it can act on
     * instead of a generic "target not clickable".
     *
     * That agreement is now enforced rather than promised, by
     * `test_the_parser_accepts_every_action_the_prompt_offers` in
     * tests/test_agent_protocol.py, which reads this literal set and the
     * rendered prompt. It is enforced because it had already broken:
     * `submit` was offered by the prompt and implemented by
     * [AuraActionExecutor], but missing here, so a model following the
     * documented search flow was told its own instructions were
     * unsupported.
     */
    val KNOWN_ACTIONS: Set<String> = setOf(
        "complete",
        "click",
        "long_click",
        "input_text",
        "clear_text",
        "scroll",
        "scroll_screen",
        "back",
        "home",
        "open_notifications",
        "open_quick_settings",
        "open_app",
        "focus",
        "submit",
    )

    /**
     * Tolerant on everything that does not change meaning.
     *
     *  - `ignoreUnknownKeys`: a model that adds "reasoning" or
     *    "confidence" has still named an action. Rejecting the whole
     *    reply over a field we do not read is the brittleness this
     *    class exists to remove.
     *  - `isLenient`: accepts unquoted keys and single quotes.
     *  - `coerceInputValues`: a null where a default exists takes the
     *    default rather than throwing.
     */
    private val json = Json {
        ignoreUnknownKeys = true
        isLenient = true
        coerceInputValues = true
    }

    sealed interface ParseResult {

        data class Success(val action: AgentAction) : ParseResult

        /**
         * [reason] is written for the model, not for a log: it says what
         * was wrong and what to send instead, and is fed back as
         * `last_action_error` on the next tick.
         */
        data class Failure(val reason: String) : ParseResult
    }

    fun parse(reply: String?): ParseResult {

        val candidate = extractJsonObject(reply)
            ?: return ParseResult.Failure(
                "Your previous reply contained no JSON object. Reply with " +
                    "ONLY the raw JSON action object, no prose and no " +
                    "explanation."
            )

        val action = runCatching { json.decodeFromString<AgentAction>(candidate) }
            .getOrElse {
                return ParseResult.Failure(
                    "Your previous reply could not be parsed as an action " +
                        "object. It must contain an \"action\" field whose " +
                        "value is one of: ${KNOWN_ACTIONS.joinToString(", ")}."
                )
            }

        val name = action.action.trim()

        if (name !in KNOWN_ACTIONS) {
            return ParseResult.Failure(
                "\"$name\" is not a supported action. Choose one of: " +
                    KNOWN_ACTIONS.joinToString(", ") + "."
            )
        }

        // Normalised, so a stray "Click " never misses the executor's
        // `when` and get reported as an unactionable target.
        return ParseResult.Success(
            if (name == action.action) action else action.copy(action = name)
        )
    }

    /**
     * The first balanced `{...}` in [reply], or null.
     *
     * Brace matching rather than "first `{` to last `}`", so a fenced
     * object followed by a sentence containing a brace still parses, and
     * a brace inside a string value - `{"message":"nice {} work"}` -
     * does not end the object early. Escapes are honoured for the same
     * reason: `"\\"` closes the string, `"\""` does not.
     *
     * A leading `[` costs nothing: scanning finds the first object
     * inside it, so a model that wrapped its single action in an array
     * is read as that action.
     */
    fun extractJsonObject(reply: String?): String? {

        val text = stripFences(reply ?: return null)

        val start = text.indexOf('{')

        if (start < 0) return null

        var depth = 0
        var inString = false
        var escaped = false

        for (i in start until text.length) {

            val c = text[i]

            if (inString) {
                when {
                    escaped -> escaped = false
                    c == '\\' -> escaped = true
                    c == '"' -> inString = false
                }
                continue
            }

            when (c) {
                '"' -> inString = true
                '{' -> depth++
                '}' -> {
                    depth--
                    if (depth == 0) return text.substring(start, i + 1)
                }
            }
        }

        // Unbalanced - a truncated reply, most likely a token limit.
        return null
    }

    /**
     * Remove a surrounding markdown code fence, if there is one.
     *
     * Only the fence markers go; the content between them is returned
     * untouched, and a reply with no fence is returned as it arrived.
     */
    private fun stripFences(reply: String): String {

        val text = reply.trim()

        if (!text.contains("```")) return text

        val open = text.indexOf("```")

        // Skip the fence and its optional language tag ("```json").
        var contentStart = open + 3

        while (contentStart < text.length && text[contentStart] != '\n') {
            // A non-newline, non-identifier character means this was not
            // a language tag - leave the text alone rather than eating
            // real content.
            if (!text[contentStart].isLetterOrDigit()) break
            contentStart++
        }

        val close = text.indexOf("```", startIndex = contentStart)

        return if (close < 0) {
            text.substring(contentStart)
        } else {
            text.substring(contentStart, close)
        }
    }
}
