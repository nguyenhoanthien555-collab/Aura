package com.aura.companion.ui.hub

import com.aura.companion.data.AuraError
import com.aura.companion.ui.components.StatusTone
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The claim the hub is allowed to make about a settings failure.
 *
 * THE DEFECT
 * ----------
 * `GET /api/health` 200 followed by *any* settings failure produced one
 * sentence: "This Aura server does not expose settings". A refused token, a
 * rate limit, a cold-start gateway error, a 500 from the settings service and a
 * payload this build cannot parse all rendered as a missing feature - on a live
 * server that has the endpoint and had just answered the health check with the
 * same token. The user was then told to update a current deployment.
 *
 * These tests pin the one-to-one mapping that replaced it, and in particular
 * pin the sentence that caused the trouble to the single state that earns it.
 */
class SettingsAccessTest {

    // ------------------------------------------------------------------
    // The mapping
    // ------------------------------------------------------------------

    @Test
    fun `a loaded document is available regardless of anything else`() {

        assertEquals(
            SettingsAccess.Available,
            settingsAccess(loaded = true, connected = true, error = null),
        )

        // A stale error must not outrank a document that arrived.
        assertEquals(
            SettingsAccess.Available,
            settingsAccess(loaded = true, connected = true, error = AuraError.Timeout),
        )

        assertTrue(SettingsAccess.Available.usable)
    }

    @Test
    fun `every HTTP outcome maps to its own state`() {

        val expected = mapOf(
            AuraError.NotSupported to SettingsAccess.NotExposed,
            AuraError.Unauthorized to SettingsAccess.AuthRequired,
            AuraError.Forbidden to SettingsAccess.Forbidden,
            AuraError.Rejected("bad") to SettingsAccess.Refused,
            AuraError.RateLimited to SettingsAccess.RateLimited,
            AuraError.ServerFailure(500) to SettingsAccess.ServerError,
            AuraError.ServerFailure(503) to SettingsAccess.ServerError,
            AuraError.Unavailable("off") to SettingsAccess.ServerError,
            AuraError.Waking to SettingsAccess.Waking,
            AuraError.Timeout to SettingsAccess.Network,
            AuraError.Offline to SettingsAccess.Network,
            AuraError.Incompatible() to SettingsAccess.Incompatible,
            AuraError.Unknown() to SettingsAccess.Unexplained,
            AuraError.NotConfigured to SettingsAccess.NotConnected,
        )

        expected.forEach { (error, access) ->
            assertEquals(
                "$error",
                access,
                settingsAccess(loaded = false, connected = true, error = error),
            )
        }
    }

    @Test
    fun `only a 404 may claim the server does not expose settings`() {

        // The whole point. `NotSupported` is what AuraRepository returns for
        // 404 and 405 and nothing else, so this sentence is now reachable from
        // exactly one HTTP outcome.
        SettingsAccess.entries.forEach { access ->
            if (access != SettingsAccess.NotExposed) {
                assertFalse(
                    "$access must not claim a missing endpoint",
                    access.reason.contains("does not expose") ||
                        access.headline.contains("unavailable on this server"),
                )
            }
        }

        assertEquals(
            "This Aura server does not expose settings",
            SettingsAccess.NotExposed.reason,
        )
    }

    @Test
    fun `a request still in flight claims nothing`() {

        // Health has answered, settings has not. The old code called this a
        // missing feature - a verdict on a request that had not come back.
        assertEquals(
            SettingsAccess.Loading,
            settingsAccess(loaded = false, connected = true, error = null),
        )

        assertNull(settingsBanner(SettingsAccess.Loading))
        assertNull(settingsNotice(SettingsAccess.Loading))
    }

    @Test
    fun `an unreachable server sends the user to the connection screen`() {

        val access = settingsAccess(loaded = false, connected = false, error = null)

        assertEquals(SettingsAccess.NotConnected, access)
        assertEquals("Connect to Aura to change this", access.reason)

        // No banner from here: the top-level error already says the server
        // could not be reached, and a second copy beside every row is noise.
        assertNull(settingsBanner(access))
    }

    // ------------------------------------------------------------------
    // What the states are allowed to say
    // ------------------------------------------------------------------

    @Test
    fun `no state explains itself with a status code`() {

        // Rule 16 again: a number is not an explanation, and "null" is not a
        // sentence. Every string a user can see is checked, not just the
        // headline.
        SettingsAccess.entries.forEach { access ->

            listOf(
                access.label,
                access.reason,
                access.headline,
                settingsBanner(access).orEmpty(),
                settingsNotice(access).orEmpty(),
            ).forEach { text ->
                listOf("unexpected response", "404", "500", "null", "exception")
                    .forEach { junk ->
                        assertFalse(
                            "$access said \"$text\", which contains \"$junk\"",
                            text.contains(junk, ignoreCase = true),
                        )
                    }
            }
        }
    }

    @Test
    fun `every failure state says something in every place it is rendered`() {

        val failures = SettingsAccess.entries - setOf(
            SettingsAccess.Available,
            SettingsAccess.NotConnected,
            SettingsAccess.Loading,
        )

        failures.forEach { access ->
            assertTrue("$access needs a label", access.label.isNotBlank())
            assertTrue("$access needs a reason", access.reason.isNotBlank())
            assertTrue("$access needs a headline", access.headline.isNotBlank())
            assertNotNull("$access needs a banner", settingsBanner(access))
            assertNotNull("$access needs a notice", settingsNotice(access))
            assertFalse("$access is not usable", access.usable)
        }
    }

    @Test
    fun `retrying is only offered where retrying could work`() {

        // Telling someone to pull to refresh a 404 is advice that cannot
        // succeed; withholding it from a cold start hides the one action that
        // will.
        assertTrue(SettingsAccess.Waking.retryable)
        assertTrue(SettingsAccess.RateLimited.retryable)
        assertTrue(SettingsAccess.Network.retryable)
        assertTrue(SettingsAccess.ServerError.retryable)

        assertFalse(SettingsAccess.NotExposed.retryable)
        assertFalse(SettingsAccess.AuthRequired.retryable)
        assertFalse(SettingsAccess.Forbidden.retryable)
        assertFalse(SettingsAccess.Incompatible.retryable)

        assertTrue(settingsBanner(SettingsAccess.Waking)!!.contains("Pull down"))
        assertFalse(settingsBanner(SettingsAccess.NotExposed)!!.contains("Pull down"))
    }

    @Test
    fun `advice to update the deployment is reserved for a server that needs it`() {

        assertTrue(
            settingsNotice(SettingsAccess.NotExposed)!!.contains("Update the deployment")
        )

        (SettingsAccess.entries - SettingsAccess.NotExposed).forEach { access ->
            assertFalse(
                "$access must not tell the user to update the server",
                settingsNotice(access).orEmpty().contains("Update the deployment"),
            )
        }
    }

    @Test
    fun `an incompatible payload is a version mismatch, not a missing route`() {

        val access = settingsAccess(
            loaded = false,
            connected = true,
            error = AuraError.Incompatible("unreadable body"),
        )

        assertEquals(SettingsAccess.Incompatible, access)

        // The endpoint answered. Saying otherwise is the exact false claim
        // this phase existed to remove.
        assertFalse(access.reason.contains("does not expose"))
        assertTrue(access.reason.contains("could not read"))
        assertTrue(settingsNotice(access)!!.contains("The route answered"))
    }

    @Test
    fun `a refused token is red and a missing feature is amber`() {

        // The colour is the fastest thing read on the screen, so it has to
        // separate "you must fix something" from "this server is behind".
        assertEquals(StatusTone.Bad, SettingsAccess.AuthRequired.tone)
        assertEquals(StatusTone.Bad, SettingsAccess.Forbidden.tone)
        assertEquals(StatusTone.Warning, SettingsAccess.NotExposed.tone)
        assertEquals(StatusTone.Neutral, SettingsAccess.Waking.tone)
        assertEquals(StatusTone.Good, SettingsAccess.Available.tone)
    }

    @Test
    fun `every label is short enough for a status row`() {

        SettingsAccess.entries.forEach { access ->
            assertTrue(
                "\"${access.label}\" is too long for a row value",
                access.label.length <= 20,
            )
        }
    }
}
