package com.aura.companion.accessibility

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The agent loop's completion guarantee.
 *
 * The chat UI keeps its loading flag set until the agent's completion
 * callback fires, so that callback must be delivered exactly once no
 * matter how the loop ends - a settled completion, a crash, or a
 * cancellation (stopAgentLoop, a newer task, or service teardown).
 * `runWithGuaranteedCompletion` is the wrapper every loop exit goes
 * through; these tests prove the guarantee without needing the
 * AccessibilityService itself (which a JVM test cannot construct).
 */
class AgentLifecycleTest {

    @Test
    fun normalCompletionDeliversTheMessageExactlyOnce() = runBlocking {
        var delivered = 0
        var message = ""

        val result = AuraAccessibilityService.runWithGuaranteedCompletion(
            block = { "App com.google.android.youtube launched successfully!" },
            onComplete = {
                delivered++
                message = it
            },
        )

        assertEquals("App com.google.android.youtube launched successfully!", result)
        assertEquals("App com.google.android.youtube launched successfully!", message)
        assertEquals(1, delivered)
    }

    @Test
    fun aCrashStillDeliversExactlyOnceAndDoesNotRethrow() = runBlocking {
        var delivered = 0
        var message = ""

        val result = AuraAccessibilityService.runWithGuaranteedCompletion(
            block = { throw IllegalStateException("boom") },
            onComplete = {
                delivered++
                message = it
            },
            crashedMessage = "The task stopped unexpectedly.",
        )

        // The crash is converted into a deliverable message, not lost.
        assertEquals("The task stopped unexpectedly.", result)
        assertEquals("The task stopped unexpectedly.", message)
        assertEquals(1, delivered)
    }

    @Test
    fun cancellationDeliversTheStoppedMessageExactlyOnceAndStaysCancelled() = runBlocking {
        var delivered = 0
        var message = ""
        var cancellationPropagated = false

        val job = launch {
            try {
                AuraAccessibilityService.runWithGuaranteedCompletion(
                    block = {
                        delay(60_000) // suspended - the realistic mid-loop point
                        "never reached"
                    },
                    onComplete = {
                        delivered++
                        message = it
                    },
                    stoppedMessage = "The task was stopped before it finished.",
                )
            } catch (e: CancellationException) {
                cancellationPropagated = true
                throw e
            }
        }

        // Let the block start and suspend, then stop the loop the way
        // stopAgentLoop / a newer task would.
        delay(100)
        job.cancel()
        job.join()

        assertTrue(cancellationPropagated)
        assertTrue(job.isCancelled)
        assertEquals(1, delivered)
        assertEquals("The task was stopped before it finished.", message)
    }

    @Test
    fun onCompleteFiresOnceEvenWhenTheBlockCompletesImmediately() = runBlocking {
        var delivered = 0

        AuraAccessibilityService.runWithGuaranteedCompletion(
            block = { "done" },
            onComplete = { delivered++ },
        )

        assertEquals(1, delivered)
    }
}
