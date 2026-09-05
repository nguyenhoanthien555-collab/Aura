package com.aura.companion.accessibility

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Regression tests for the structured tool protocol - the device half
 * of the agent migration. These pin what replaced `KNOWN_ACTIONS`: a
 * catalogue with argument shapes, validation before execution, and
 * stable failure codes instead of prose.
 */
class AgentToolProtocolTest {

    private fun arguments(vararg pairs: Pair<String, String>): JsonObject =
        buildJsonObject {
            pairs.forEach { (key, value) -> put(key, value) }
        }

    // ------------------------------------------------------------------
    // The catalogue replaces KNOWN_ACTIONS
    // ------------------------------------------------------------------

    @Test
    fun the_catalogue_declares_the_same_capabilities_the_server_mirrors() {
        val expected = setOf(
            "android.get_foreground_app",
            "android.get_ui_tree",
            "android.find_node",
            "android.screenshot",
            "android.tap",
            "android.long_press",
            "android.swipe",
            "android.type_text",
            "android.press_key",
            "android.back",
            "android.home",
            "android.launch_app",
            "android.wait_for",
            "android.verify",
            // Phase 5A. The count is deliberately not in this test's name
            // any more: it was "fourteen", and a name that has to change
            // every time a capability lands is a name that stops being
            // read. The set is the contract; the server's AndroidProvider
            // mirrors exactly this.
            "android.list_apps",
        )

        assertEquals(expected, DeviceToolCatalog.TOOLS.keys)
    }

    @Test
    fun reads_are_safe_and_mutations_are_flagged() {

        assertFalse(DeviceToolCatalog.isMutating("android.get_ui_tree"))
        assertFalse(DeviceToolCatalog.isMutating("android.verify"))

        assertTrue(DeviceToolCatalog.isMutating("android.tap"))
        assertTrue(DeviceToolCatalog.isMutating("android.launch_app"))
        assertTrue(DeviceToolCatalog.isMutating("android.type_text"))
    }

    // ------------------------------------------------------------------
    // Validation before execution
    // ------------------------------------------------------------------

    @Test
    fun a_launch_with_a_package_is_accepted() {
        val validation = DeviceToolCatalog.validate(
            "android.launch_app", arguments("package" to "com.y")
        )

        assertTrue(validation is DeviceToolCatalog.Validation.Ok)
    }

    @Test
    fun a_launch_without_a_package_is_rejected_with_the_missing_name() {
        val validation = DeviceToolCatalog.validate(
            "android.launch_app", JsonObject(emptyMap())
        )

        val bad = validation as DeviceToolCatalog.Validation.BadArguments

        assertTrue(bad.reason.contains("package"))
    }

    @Test
    fun an_argument_outside_the_contract_is_named_not_ignored() {
        val validation = DeviceToolCatalog.validate(
            "android.launch_app", arguments(
                "package" to "com.y",
                "confidence" to "0.9",
            )
        )

        val bad = validation as DeviceToolCatalog.Validation.BadArguments

        assertTrue(bad.reason.contains("confidence"))
    }

    @Test
    fun an_unknown_tool_is_recognised_as_such() {
        val validation = DeviceToolCatalog.validate(
            "android.open_youtube_magic", JsonObject(emptyMap())
        )

        assertTrue(validation is DeviceToolCatalog.Validation.UnknownTool)
    }

    @Test
    fun tap_accepts_either_targeting_mode() {
        assertTrue(
            DeviceToolCatalog.validate(
                "android.tap", arguments("text" to "Search")
            ) is DeviceToolCatalog.Validation.Ok
        )

        assertTrue(
            DeviceToolCatalog.validate(
                "android.tap", arguments("node_id" to "n1")
            ) is DeviceToolCatalog.Validation.Ok
        )
    }

    // ------------------------------------------------------------------
    // Wire round-trips
    // ------------------------------------------------------------------

    @Test
    fun a_directive_survives_serialisation() {
        val json = Json { ignoreUnknownKeys = true }

        val incoming = """
            {
              "tool_call_id": "call_abc123456789abcd",
              "tool": "android.launch_app",
              "arguments": {"package": "com.google.android.youtube"},
              "unknown_future_field": true
            }
        """.trimIndent()

        val directive = json.decodeFromString(
            ToolCallDirective.serializer(), incoming
        )

        assertEquals("android.launch_app", directive.tool)
        assertEquals(
            "com.google.android.youtube",
            directive.arguments["package"]?.toString()?.trim('"')
        )
    }

    @Test
    fun a_failure_report_carries_a_stable_code() {
        val report = ToolResultReport(
            toolCallId = "call_abc123456789abcd",
            tool = "android.tap",
            ok = false,
            error = ToolError("NODE_NOT_FOUND", "no visible node"),
        )

        val encoded = Json.encodeToString(
            ToolResultReport.serializer(), report
        )

        assertTrue(encoded.contains("NODE_NOT_FOUND"))

        val decoded = Json.decodeFromString(
            ToolResultReport.serializer(), encoded
        )

        assertEquals(false, decoded.ok)
        assertEquals("NODE_NOT_FOUND", decoded.error?.code)
        assertTrue(decoded.postcondition == null)
    }

    @Test
    fun a_verified_result_carries_postcondition_evidence() {
        val report = ToolResultReport(
            toolCallId = "call_abc123456789abcd",
            tool = "android.type_text",
            ok = true,
            result = buildJsonObject { put("typed", 9) },
            postcondition = buildJsonObject {
                put("verified", true)
                put("expected_text", "Minecraft")
            },
        )

        val encoded = Json.encodeToString(
            ToolResultReport.serializer(), report
        )

        assertTrue(encoded.contains("\"verified\":true"))
        assertTrue(encoded.contains("Minecraft"))
    }
}