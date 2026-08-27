package com.aura.companion.accessibility

import com.aura.companion.data.AuraRepository
import com.aura.companion.data.AuraResult
import com.aura.companion.data.remote.AgentRunSnapshotDto
import com.aura.companion.data.remote.AgentStepRequestDto
import com.aura.companion.data.remote.ObservationDto
import com.aura.companion.data.remote.ToolCallDto
import com.aura.companion.data.remote.ToolResultEnvelopeDto
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject

/**
 * The device-side agent TRANSPORT loop - and nothing more (PART 13).
 *
 * The old `runAgentSteps` asked the server for a prose action, parsed
 * it, decided retries, tracked its own progress in strings, and decided
 * when the task was done. None of that lives here anymore. This driver
 * is a courier with one fixed cycle:
 *
 *     capture fresh observation → POST /api/agent/step
 *     ← tool_calls? execute each through the dispatcher
 *     ← final? deliver the server's verdict verbatim
 *
 * Every decision - the next action, retries, completion, verification -
 * belongs to the server's AgentRuntime. This class cannot complete a
 * task on its own because it has no idea what "done" means; it only
 * knows how to carry observations up and structured results down.
 */
class AgentRunDriver(
    private val repository: AuraRepository,
    private val dispatcher: DeviceToolExecutor,
    /** One FRESH observation per step; never cached. */
    private val observe: () -> ObservationDto,
    /** A dedicated session: agent runs stay out of the chat transcript. */
    private val sessionId: String = DEVICE_AGENT_SESSION,
    private val maxSteps: Int = STEP_CEILING,
) {

    suspend fun run(goal: String): String {

        var runId = ""
        var pendingResults: List<ToolResultEnvelopeDto> = emptyList()
        var pendingObservations: List<ObservationDto> = emptyList()
        var steps = 0

        while (steps < maxSteps) {
            steps++

            val step = AgentStepRequestDto(
                sessionId = sessionId,
                // The goal names the task once; after that the run id does.
                goal = if (runId.isEmpty()) goal else "",
                runId = runId,
                // The fresh capture first, then whatever the executed
                // tools saw. The server records all of them under this
                // run, which is what makes a tool result's observation id
                // resolve to something.
                observations = listOf(observe()) + pendingObservations,
                toolResults = pendingResults,
            )
            pendingResults = emptyList()
            pendingObservations = emptyList()

            val snapshot = when (val result = repository.agentStep(step)) {
                is AuraResult.Ok -> result.value
                is AuraResult.Failed ->
                    return "Aura could not reach the server, so the task " +
                        "was stopped before anything was done."
            }

            if (snapshot.runId.isNotEmpty()) runId = snapshot.runId

            val directive = snapshot.directive

            if (directive == null || directive.type != "tool_calls") {
                return finalMessage(snapshot)
            }

            for (call in directive.toolCalls) {
                val report = dispatcher.execute(
                    ToolCallDirective(
                        toolCallId = call.toolCallId,
                        tool = call.tool,
                        arguments = call.arguments,
                    )
                )

                pendingResults += report.toEnvelope(call)

                report.observation?.let {
                    pendingObservations += it.toDto()
                }
            }
        }

        // The step ceiling is a transport guard, not a decision: the
        // server's own budget stops a run long before this, so reaching
        // it means the exchange itself went wrong.
        return "The task needed more than $maxSteps exchanges with the " +
            "server, so it was stopped rather than left half-done."
    }

    /**
     * The server's verdict, delivered verbatim - this device does not
     * editorialise about whether a task succeeded.
     */
    private fun finalMessage(snapshot: AgentRunSnapshotDto): String {

        snapshot.directive?.text?.takeIf { it.isNotBlank() }
            ?.let { return it }

        return when {
            snapshot.status == "cancelled" -> "Task cancelled."
            snapshot.stopReason == "goal_verified" -> "Task completed."
            else -> "Aura could not finish the task" +
                (snapshot.stopDetail.takeIf { it.isNotBlank() }
                    ?.let { ": $it" } ?: ".")
        }
    }

    /** Map a report onto the envelope shape the runtime folds back. */
    private fun ToolResultReport.toEnvelope(
        call: ToolCallDto,
    ): ToolResultEnvelopeDto = ToolResultEnvelopeDto(
        toolCallId = toolCallId.ifEmpty { call.toolCallId },
        tool = tool,
        arguments = call.arguments,
        ok = ok,
        result = result,
        error = error?.let {
            buildJsonObject {
                put("code", JsonPrimitive(it.code))
                put("message", JsonPrimitive(it.message))
            }
        },
        postcondition = postcondition,
        observationId = observationId,
    )

    companion object {
        /**
         * The session agent runs belong to.
         *
         * Separate from the chat session on purpose: a run's tool
         * exchanges are not conversation, and folding them into the chat
         * transcript would put dozens of machine turns in front of the
         * user's own history.
         */
        const val DEVICE_AGENT_SESSION = "device-agent"

        /**
         * The most step exchanges one run may cost this device.
         *
         * A transport backstop against a server that keeps asking - the
         * real budget is the runtime's, which stops well inside this.
         */
        const val STEP_CEILING = 24
    }
}

/** The device's observation payload in the wire shape /step accepts. */
internal fun ObservationPayload.toDto(): ObservationDto = ObservationDto(
    kind = kind,
    source = source,
    data = data,
    observationId = observationId,
    observedAt = observedAt,
    contentHash = contentHash,
)

/**
 * Transport-agnostic execution seam: the real dispatcher implements it,
 * and JVM tests substitute a scripted one.
 */
interface DeviceToolExecutor {
    suspend fun execute(directive: ToolCallDirective): ToolResultReport
}

/** Optional runtime inventory reporter implemented by the real dispatcher. */
interface DeviceCapabilityReporter {
    fun capabilityStatus(): Map<String, com.aura.companion.data.remote.DeviceCapabilityStatusDto>
}
