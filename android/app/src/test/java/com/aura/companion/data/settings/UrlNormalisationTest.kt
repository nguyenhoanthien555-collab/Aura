package com.aura.companion.data.settings

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * §21: "server URL configuration".
 *
 * [SettingsStore.normaliseUrl] is the whole of the app's URL handling, and
 * it runs on what a person typed into a phone keyboard. Everything below
 * is a form someone will actually enter.
 */
class UrlNormalisationTest {

    @Test
    fun `bare host and port assumed local and given http`() {
        assertEquals(
            "http://192.168.1.10:8000/",
            SettingsStore.normaliseUrl("192.168.1.10:8000"),
        )
    }

    @Test
    fun `public host assumed https`() {
        assertEquals(
            "https://aura.onrender.com/",
            SettingsStore.normaliseUrl("aura.onrender.com"),
        )
    }

    @Test
    fun `explicit scheme is never overridden`() {
        // Someone running a proxy on their LAN over TLS, or testing a
        // public host over plaintext. Both are their decision, not ours.
        assertEquals("https://192.168.1.10/", SettingsStore.normaliseUrl("https://192.168.1.10"))
        assertEquals("http://example.com/", SettingsStore.normaliseUrl("http://example.com"))
    }

    @Test
    fun `trailing slash is added exactly once`() {
        // Retrofit resolves relative paths against the base URL, and a
        // missing slash silently drops the last path segment.
        assertEquals("https://host.dev/", SettingsStore.normaliseUrl("https://host.dev"))
        assertEquals("https://host.dev/", SettingsStore.normaliseUrl("https://host.dev/"))
    }

    @Test
    fun `a path in the base URL survives normalisation`() {
        // A deployment behind a path prefix - /aura on a shared host.
        assertEquals(
            "https://host.dev/aura/",
            SettingsStore.normaliseUrl("https://host.dev/aura"),
        )
    }

    @Test
    fun `surrounding whitespace is stripped`() {
        // Pasting from a chat message brings a trailing space with it.
        assertEquals(
            "https://aura.example.com/",
            SettingsStore.normaliseUrl("  https://aura.example.com  "),
        )
    }

    @Test
    fun `blank stays blank rather than becoming a scheme`() {
        // Must stay empty: `isConfigured` is what the UI uses to decide
        // whether to show the first-run prompt, and "https://" is not
        // blank.
        assertEquals("", SettingsStore.normaliseUrl(""))
        assertEquals("", SettingsStore.normaliseUrl("   "))
    }

    @Test
    fun `localhost and the emulator loopback are treated as local`() {
        assertEquals("http://localhost:8000/", SettingsStore.normaliseUrl("localhost:8000"))
        assertEquals("http://10.0.2.2:8000/", SettingsStore.normaliseUrl("10.0.2.2:8000"))
    }

    @Test
    fun `mdns names are local`() {
        assertEquals("http://desk.local:8000/", SettingsStore.normaliseUrl("desk.local:8000"))
    }

    @Test
    fun `isSecure reports the scheme actually stored`() {
        assertTrue(AuraSettings(serverUrl = "https://aura.example.com/").isSecure)
        assertFalse(AuraSettings(serverUrl = "http://192.168.1.10:8000/").isSecure)
    }

    @Test
    fun `isConfigured is false until a URL exists`() {
        assertFalse(AuraSettings().isConfigured)
        assertTrue(AuraSettings(serverUrl = "https://aura.example.com/").isConfigured)
    }

    @Test
    fun `toString never reveals the token`() {
        // The single most common way a credential reaches logcat is an
        // interpolation of the whole settings object.
        val rendered = AuraSettings(
            serverUrl = "https://aura.example.com/",
            authToken = "super-secret-token-value",
        ).toString()

        assertFalse(rendered.contains("super-secret-token-value"))
        assertTrue(rendered.contains("***"))
    }
}
