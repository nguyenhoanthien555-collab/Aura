package com.aura.companion.accessibility

import android.util.Log
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.AuraResult
import com.aura.companion.data.remote.DeviceInvocationDto
import com.aura.companion.data.remote.DeviceResultReportDto
import com.aura.companion.data.settings.SettingsProvider
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put

/**
 * The device end of the polling transport (PARTS 1-5).
 *
 * FastAPI cannot push to a handset, so `/api/device/invoke` blocks its
 * caller while this loop fetches the queued invocation from
 * `/api/device/poll`, executes it through the one
 * [AccessibilityToolDispatcher], and answers `/api/device/results`.
 * Without this loop the server's registry advertises android tools that
 * nothing on the phone would ever pick up - which is precisely the gap
 * between "the endpoint exists" and "the device executes".
 *
 * It carries no agent logic whatsoever. It does not know what a run is,
 * cannot decide an action, and never sees model output. Its whole
 * contract is: fetch one invocation, dispatch it, report the structured
 * result, repeat.
 */
class DeviceInvocationPoller(
    private val repository: AuraRepository,
    private val settings: SettingsProvider,
    private val dispatcher: DeviceToolExecutor,
) {
    /**
     * Reports awaiting acknowledgement, keyed by invocation identity.
     * A failed result POST must never cause the next poll to execute the
     * same mutating invocation again; it only retries this cached report.
     */
    private val completedReports = LinkedHashMap<String, DeviceResultReportDto>()

    /**
     * Poll until the enclosing scope is cancelled.
     *
     * The cadence is deliberately adaptive rather than fixed. A caller
     * of `/invoke` is blocked for as long as it takes this loop to
     * notice, so idling slowly would add that delay to every single tool
     * call in an agent run; polling fast forever would cost battery for
     * nothing on a phone that is doing no agent work at all. So: quick
     * while work is flowing, slower after a stretch of silence, and
     * immediately quick again the moment an invocation appears.
     */
    suspend fun pollForever() {

        var idlePolls = 0

        while (true) {

            if (!settings.current.isConfigured) {
                // No server, nothing to poll. Checked every cycle rather
                // than once, because a user can configure Aura long after
                // the service connected.
                delay(UNCONFIGURED_DELAY_MS)
                continue
            }

            val timeoutS = if (idlePolls >= IDLE_THRESHOLD) IDLE_POLL_TIMEOUT_S else ACTIVE_POLL_TIMEOUT_S

            val invocations = when (
                val result = repository.pollDeviceInvocations(deviceId(), timeoutS = timeoutS)
            ) {
                is AuraResult.Ok -> result.value.invocations
                is AuraResult.Failed -> {
                    // A server that is down or unreachable is a normal
                    // state for a phone, not an error worth spinning on.
                    // The reason is logged; nothing about the token is.
                    Log.d(TAG, "poll unavailable: ${result.error::class.simpleName}")
                    delay(BACKOFF_DELAY_MS)
                    continue
                }
            }

            if (invocations.isEmpty()) {
                idlePolls++
                // Server-side long polling already held the connection up to timeoutS.
                // Minimal pause between consecutive long-poll cycles to yield.
                delay(if (idlePolls >= IDLE_THRESHOLD) 200L else 20L)
                continue
            }

            idlePolls = 0

            for (invocation in invocations) {
                answer(invocation)
            }

            // Straight back round: the queue may hold the next call of a
            // multi-tool round, and waiting would add a delay the caller
            // is already blocked on.
        }
    }

    /** Execute one invocation and deliver its structured report. */
    private suspend fun answer(invocation: DeviceInvocationDto) {

        val submission = completedReports[invocation.invocationId] ?: run {
            val report = try {
                dispatcher.execute(
                    ToolCallDirective(
                        toolCallId = invocation.toolCallId,
                        tool = invocation.tool,
                        arguments = invocation.arguments,
                    )
                )
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                Log.w(TAG, "dispatch failed for ${invocation.tool}", error)
                ToolResultReport(
                    toolCallId = invocation.toolCallId,
                    tool = invocation.tool,
                    arguments = invocation.arguments,
                    ok = false,
                    error = ToolError(
                        code = AccessibilityToolDispatcher.EXECUTION_FAILED,
                        message = error.message ?: "the tool could not run",
                    ),
                )
            }

            DeviceResultReportDto(
                invocationId = invocation.invocationId,
                ok = report.ok,
                result = report.result,
                error = report.error?.let {
                    buildJsonObject {
                        put("code", it.code)
                        put("message", it.message)
                    }
                },
                postcondition = report.postcondition,
                observationId = report.observationId,
                observation = report.observation?.let {
                    PROTOCOL_JSON.encodeToJsonElement(it).jsonObject
                },
            ).also { completedReports[invocation.invocationId] = it }
        }

        when (val delivered = repository.submitDeviceResults(
            deviceId(), listOf(submission),
        )) {
            is AuraResult.Ok -> {
                // Accepted and rejected are both terminal acknowledgements:
                // rejected means the caller timed out/cancelled and no longer
                // owns this invocation. In neither case may it execute again.
                completedReports.remove(invocation.invocationId)
                if (delivered.value.rejected.isNotEmpty()) {
                    Log.d(TAG, "result refused for ${invocation.tool}")
                }
            }

            is AuraResult.Failed -> {
                // Keep the completed report. The server will return the same
                // invocation on the next poll, which retries delivery only.
                trimCompletedReports()
                Log.w(TAG, "result not delivered: ${delivered.error::class.simpleName}")
            }
        }
    }

    private fun trimCompletedReports() {
        while (completedReports.size > MAX_CACHED_REPORTS) {
            completedReports.remove(completedReports.keys.first())
        }
    }

    private fun deviceId(): String = settings.current.deviceId

    private companion object {
        const val TAG = "AuraDevicePoller"

        /** Long-poll server wait budgets. */
        const val ACTIVE_POLL_TIMEOUT_S = 10.0
        const val IDLE_POLL_TIMEOUT_S = 20.0

        /** Empty polls before easing off. */
        const val IDLE_THRESHOLD = 5

        const val BACKOFF_DELAY_MS = 5_000L
        const val UNCONFIGURED_DELAY_MS = 10_000L
        const val MAX_CACHED_REPORTS = 100
    }
}

internal val PROTOCOL_JSON: Json = Json { encodeDefaults = true }

/** The observation payload as the wire object `/results` accepts. */
internal fun ObservationPayload.toJsonObject(): JsonObject =
    PROTOCOL_JSON.encodeToJsonElement(this).jsonObject
