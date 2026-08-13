package com.aura.companion.accessibility

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * That `open_app` can resolve a launch intent at all.
 *
 * WHY THIS TEST EXISTS
 * --------------------
 * "mở YouTube" consumed all ten agent steps and reported
 * "Task timed out: maximum number of steps reached." Every step reached
 * the model and came back 200; the model's action was parsed fine. What
 * failed was the device half:
 *
 *     "open_app" -> {
 *         val pkg = action.packageName ?: return false
 *         val intent = service.packageManager.getLaunchIntentForPackage(pkg)
 *         if (intent != null) { ...startActivity...; true } else { false }
 *     }
 *
 * `getLaunchIntentForPackage` resolves the target's MAIN/LAUNCHER
 * activity, which is a *package query*. Since Android 11 (API 30)
 * queries are filtered to what the manifest declares an interest in, and
 * this manifest declared none while `targetSdk` is 35. So the call
 * returned null for every third-party app - including a correctly named,
 * installed one - `open_app` returned false on both attempts, and the
 * loop burned the step budget relaunching an app it could never see.
 *
 * Provider-independent: nothing here depends on which model answered.
 * The Mistral failover in the logs was concurrent, not causal.
 *
 * Asserted against the manifest file because that is where the property
 * lives and where it was false. A JVM test has no PackageManager, and
 * mocking one would assert that a mock was called - the device procedure
 * in the report covers the other half.
 */
class OpenAppLaunchabilityTest {

    @Test
    fun `the manifest declares the launcher query open_app depends on`() {

        val manifest = manifestText()

        // The declaration must be a real <queries> block, not a comment
        // mentioning one.
        val queries = Regex("""<queries>(.*?)</queries>""", RegexOption.DOT_MATCHES_ALL)
            .find(manifest)
            ?.groupValues
            ?.get(1)

        assertTrue(
            "AndroidManifest.xml declares no <queries> block, so " +
                "getLaunchIntentForPackage is filtered and open_app can " +
                "never resolve a launch intent on Android 11+",
            queries != null,
        )

        val declared = queries.orEmpty()

        assertTrue(
            "the <queries> block must declare the MAIN action that " +
                "getLaunchIntentForPackage resolves",
            "android.intent.action.MAIN" in declared,
        )

        assertTrue(
            "the <queries> block must declare the LAUNCHER category that " +
                "getLaunchIntentForPackage resolves",
            "android.intent.category.LAUNCHER" in declared,
        )
    }

    @Test
    fun `visibility is declared by intent rather than by querying every package`() {

        // QUERY_ALL_PACKAGES answers far more than open_app asks and needs
        // a policy declaration on Play. If it ever appears here it should
        // be a deliberate decision, not a reflex fix for this bug.
        assertFalse(
            "QUERY_ALL_PACKAGES is broader than open_app needs; the " +
                "MAIN/LAUNCHER <queries> intent is the scoped equivalent",
            "QUERY_ALL_PACKAGES" in manifestText(),
        )
    }

    // ------------------------------------------------------------------
    // The error the model is told, when a launch genuinely cannot happen
    // ------------------------------------------------------------------

    @Test
    fun `a failed open_app names the package instead of blaming a node`() {

        val reason = AuraAccessibilityService.failureReason(
            AgentAction(action = "open_app", packageName = "com.youtube.android"),
        )

        assertTrue(
            "the model cannot correct a package name it is not shown: $reason",
            "com.youtube.android" in reason,
        )

        // `node_id` is null for open_app, so the old generic sentence
        // rendered as "on null" and blamed a target that does not exist.
        assertFalse("open_app has no target node to blame: $reason", "null" in reason)
        assertFalse("open_app has no target node to blame: $reason", "not clickable" in reason)
    }

    @Test
    fun `a missing package is reported as a missing field`() {

        val reason = AuraAccessibilityService.failureReason(
            AgentAction(action = "open_app"),
        )

        assertTrue("must name the field to send: $reason", "package" in reason)
        assertFalse("nothing was tried, so nothing can be quoted: $reason", "\"\"" in reason)
    }

    @Test
    fun `every other action keeps the node-based message`() {

        // Unchanged on purpose: a click really does reference a node from
        // the tree the model was just shown, so the original sentence is
        // accurate there.
        val reason = AuraAccessibilityService.failureReason(
            AgentAction(action = "click", nodeId = "node_7"),
        )

        assertTrue(reason, "node_7" in reason)
        assertTrue(reason, "not clickable" in reason)
    }

    /**
     * The manifest, found from wherever Gradle set the working directory,
     * with XML comments removed.
     *
     * Comments are stripped so that prose *about* a declaration can never
     * be mistaken for the declaration - neither a commented-out
     * `<queries>` block satisfying the first test, nor this file's own
     * note explaining why QUERY_ALL_PACKAGES was not used failing the
     * second.
     *
     * Fails loudly rather than skipping: a test that silently cannot
     * find the file would pass while the bug was present, which is the
     * one outcome that must not be possible here.
     */
    private fun manifestText(): String {

        val candidates = listOf(
            "src/main/AndroidManifest.xml",
            "app/src/main/AndroidManifest.xml",
            "android/app/src/main/AndroidManifest.xml",
        )

        val found = candidates.map { File(it) }.firstOrNull { it.isFile }
            ?: throw AssertionError(
                "AndroidManifest.xml not found from ${File("").absolutePath}; " +
                    "tried $candidates"
            )

        return found.readText()
            .replace(Regex("""<!--.*?-->""", RegexOption.DOT_MATCHES_ALL), "")
    }
}
