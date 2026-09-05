package com.aura.companion.accessibility

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The device half of Phase 5A: `android.list_apps` and the installed-app
 * inventory it reports.
 *
 * WHY THESE TESTS LOOK LIKE THIS
 * ------------------------------
 * A JVM unit test has no `PackageManager`, and this module carries no
 * Robolectric and no mocking framework - the same constraint
 * [OpenAppLaunchabilityTest] documents. So the Android call sites live in
 * `PlatformPackageSource`, which is thin enough to read, and every rule
 * about what an inventory *means* lives in [AppInventory], which is pure.
 * These tests drive that pure half through a scripted [PackageSource],
 * which is the seam the production code was written around rather than a
 * mock bolted on afterwards.
 *
 * Nothing here needs, or claims, a real device.
 */
class AppInventoryTest {

    // ------------------------------------------------------------------
    // Doubles
    // ------------------------------------------------------------------

    /** A scripted platform: whatever the test says the device reported. */
    private class FakePackages(
        private val launcher: List<String> = emptyList(),
        private val installed: List<RawPackage> = emptyList(),
    ) : PackageSource {
        override fun launcherPackages(): List<String> = launcher
        override fun installedPackages(): List<RawPackage> = installed
    }

    /** A platform that fails, the way a dead PackageManager binder does. */
    private class BrokenPackages : PackageSource {
        override fun launcherPackages(): List<String> =
            throw IllegalStateException("package manager is gone")

        override fun installedPackages(): List<RawPackage> =
            throw IllegalStateException("package manager is gone")
    }

    private fun app(
        name: String?,
        label: String? = null,
        enabled: Boolean? = null,
        version: String? = null,
    ) = RawPackage(name, label, enabled, version)

    private fun packagesOf(result: JsonObject): List<JsonObject> =
        result["packages"]!!.jsonArray.map { it.jsonObject }

    private fun countOf(result: JsonObject): Int =
        result["count"]!!.jsonPrimitive.int

    // ------------------------------------------------------------------
    // Catalogue and capability wiring
    // ------------------------------------------------------------------

    @Test
    fun list_apps_is_a_declared_read_with_no_arguments() {

        val validation = DeviceToolCatalog.validate(
            "android.list_apps", JsonObject(emptyMap()),
        )

        assertTrue(
            "a bare list_apps must validate",
            validation is DeviceToolCatalog.Validation.Ok,
        )
        assertFalse(
            "an inventory read must never be flagged as mutating",
            DeviceToolCatalog.isMutating("android.list_apps"),
        )
    }

    @Test
    fun list_apps_rejects_arguments_it_does_not_declare() {

        // A filter argument is the tempting one, and the reason it is
        // refused: "is WhatsApp installed?" answered by a filtered query
        // returns an empty list whether the app is absent or the filter
        // was wrong, and those must not look the same.
        val validation = DeviceToolCatalog.validate(
            "android.list_apps",
            buildJsonObject { put("package", "com.whatsapp") },
        )

        assertTrue(
            "an undeclared argument is a contract disagreement",
            validation is DeviceToolCatalog.Validation.BadArguments,
        )
    }

    @Test
    fun list_apps_is_gated_on_the_app_inventory_capability() {

        assertEquals(
            "android.app_inventory",
            AccessibilityToolDispatcher.CAPABILITY_BY_TOOL["android.list_apps"],
        )
    }

    @Test
    fun every_gated_capability_is_one_this_device_actually_reports() {

        // The silent failure this catches: a tool gated on a capability
        // `capabilityStatus()` never lists resolves to state "UNKNOWN",
        // so `execute` refuses it with CAPABILITY_UNKNOWN before the
        // dispatch table is ever reached - on the handset only, where no
        // offline test would see it.
        val reported =
            AccessibilityToolDispatcher.ACCESSIBILITY_CAPABILITIES.toSet() +
                "android.screen_capture"

        val gated = AccessibilityToolDispatcher.CAPABILITY_BY_TOOL.values.toSet()

        assertEquals(
            "capabilities gated on but never reported by this device",
            emptySet<String>(),
            gated - reported,
        )
    }

    @Test
    fun every_catalogue_tool_is_gated_on_a_capability() {

        val ungated = DeviceToolCatalog.TOOLS.keys -
            AccessibilityToolDispatcher.CAPABILITY_BY_TOOL.keys

        assertEquals(
            "a declared tool with no capability gate executes ungated",
            emptySet<String>(),
            ungated,
        )
    }

    // ------------------------------------------------------------------
    // Enumeration
    // ------------------------------------------------------------------

    @Test
    fun an_empty_device_produces_an_empty_inventory() {

        val entries = AppInventory.collect(FakePackages())

        assertEquals(emptyList<AppInventory.Entry>(), entries)
        assertEquals(0, countOf(AppInventory.result(entries, "d1", 12.0)))
    }

    @Test
    fun multiple_packages_are_all_reported_in_a_deterministic_order() {

        val entries = AppInventory.collect(
            FakePackages(
                launcher = listOf("com.whatsapp", "com.android.chrome"),
                installed = listOf(
                    app("com.whatsapp", "WhatsApp", true, "2.24"),
                    app("com.android.chrome", "Chrome", true, "120.0"),
                    app("com.aura.companion", "Aura", true, "0.1.0"),
                ),
            )
        )

        // Sorted, because an inventory whose order moves between two
        // reads makes "did this change?" undecidable.
        assertEquals(
            listOf("com.android.chrome", "com.aura.companion", "com.whatsapp"),
            entries.map { it.packageName },
        )
    }

    @Test
    fun launchability_comes_from_the_launcher_query_not_from_a_guess() {

        val entries = AppInventory.collect(
            FakePackages(
                launcher = listOf("com.launchable"),
                installed = listOf(
                    app("com.launchable", "Openable"),
                    app("com.service.only", "Background thing"),
                ),
            )
        ).associateBy { it.packageName }

        assertTrue(entries.getValue("com.launchable").launchable)
        assertFalse(entries.getValue("com.service.only").launchable)
    }

    @Test
    fun a_disabled_application_is_reported_disabled_and_still_listed() {

        // Present-but-disabled is a real state and a useful one: "it is
        // installed but you turned it off" is a different answer from
        // "it is not installed", and dropping the entry would collapse
        // them into the second.
        val entries = AppInventory.collect(
            FakePackages(
                installed = listOf(app("com.disabled", "Off", enabled = false)),
            )
        )

        assertEquals(1, entries.size)
        assertEquals(false, entries[0].enabled)
    }

    @Test
    fun a_package_the_launcher_saw_but_the_package_query_did_not_is_kept() {

        // Package visibility filtering can answer the two queries
        // differently. The launchable fact is real; nothing else about
        // the package is known, and nothing else is filled in.
        val entries = AppInventory.collect(
            FakePackages(launcher = listOf("com.launcher.only"))
        )

        assertEquals(1, entries.size)
        assertEquals("com.launcher.only", entries[0].packageName)
        assertTrue(entries[0].launchable)
        assertNull(entries[0].label)
        assertNull(entries[0].enabled)
        assertNull(entries[0].versionName)
    }

    @Test
    fun a_package_seen_by_both_queries_produces_exactly_one_entry() {

        val entries = AppInventory.collect(
            FakePackages(
                launcher = listOf("com.both"),
                installed = listOf(app("com.both", "Both")),
            )
        )

        assertEquals(1, entries.size)
        assertTrue(entries[0].launchable)
        assertEquals("Both", entries[0].label)
    }

    // ------------------------------------------------------------------
    // Malformed platform data
    // ------------------------------------------------------------------

    @Test
    fun unusable_package_names_are_dropped_rather_than_repaired() {

        // An entry that cannot be identified cannot be evidence about
        // anything, so it is not reported at all. Inventing a name would
        // put a package into the answer that does not exist.
        val entries = AppInventory.collect(
            FakePackages(
                launcher = listOf("", "   "),
                installed = listOf(
                    app(null, "no name"),
                    app("", "empty name"),
                    app("   ", "blank name"),
                    app("com.real", "Real"),
                ),
            )
        )

        assertEquals(listOf("com.real"), entries.map { it.packageName })
    }

    @Test
    fun missing_optional_fields_are_omitted_never_defaulted() {

        val entries = AppInventory.collect(
            FakePackages(installed = listOf(app("com.minimal")))
        )

        val entry = packagesOf(AppInventory.result(entries, "d1", 12.0)).single()

        assertEquals("com.minimal", entry["package"]!!.jsonPrimitive.content)
        // Absent, not `false` and not `""`: a fabricated value is
        // indistinguishable downstream from an observed one.
        assertNull(entry["label"])
        assertNull(entry["enabled"])
        assertNull(entry["version_name"])
        // Launchability is the one fact always known - the launcher query
        // either listed it or it did not.
        assertFalse(entry["launchable"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun blank_labels_and_versions_are_treated_as_absent() {

        val entries = AppInventory.collect(
            FakePackages(installed = listOf(app("com.blank", "  ", true, "")))
        )

        assertNull(entries[0].label)
        assertNull(entries[0].versionName)
        assertEquals(true, entries[0].enabled)
    }

    @Test
    fun a_duplicated_package_stays_duplicated() {

        // The transport reports what the device reported. Silently
        // collapsing a duplicate would hide a real platform oddity, and
        // whether it matters is the reasoning layer's call, not this
        // enumeration's.
        val entries = AppInventory.collect(
            FakePackages(
                installed = listOf(app("com.twice"), app("com.twice")),
            )
        )

        assertEquals(listOf("com.twice", "com.twice"), entries.map { it.packageName })
    }

    @Test
    fun a_failing_package_manager_throws_instead_of_reporting_no_apps() {

        // The failure mode this exists to prevent: swallowing the error
        // and returning an empty list, which the wire payload would
        // report as a perfectly well-formed `count: 0` SUCCESS - AURA
        // then telling the owner they have no apps installed. Throwing
        // reaches the dispatcher's catch and becomes EXECUTION_FAILED.
        var raised = false

        try {
            AppInventory.collect(BrokenPackages())
        } catch (error: IllegalStateException) {
            raised = true
        }

        assertTrue("a broken platform must not read as an empty device", raised)
    }

    // ------------------------------------------------------------------
    // The structured success report
    // ------------------------------------------------------------------

    @Test
    fun the_success_payload_carries_the_declared_inventory_shape() {

        val entries = AppInventory.collect(
            FakePackages(
                launcher = listOf("com.whatsapp"),
                installed = listOf(app("com.whatsapp", "WhatsApp", true, "2.24")),
            )
        )

        val result = AppInventory.result(entries, "device-7", 1_756_000_000.5)

        assertEquals(1, countOf(result))
        assertEquals("android.package_manager",
            result["source"]!!.jsonPrimitive.content)
        assertEquals("device-7", result["device_id"]!!.jsonPrimitive.content)
        // The server's transport check rejects an inventory without this,
        // because a list with no observation time cannot be judged fresh.
        assertNotEquals(null, result["observed_at"])

        val entry = packagesOf(result).single()
        assertEquals("com.whatsapp", entry["package"]!!.jsonPrimitive.content)
        assertEquals("WhatsApp", entry["label"]!!.jsonPrimitive.content)
        assertEquals("2.24", entry["version_name"]!!.jsonPrimitive.content)
        assertTrue(entry["launchable"]!!.jsonPrimitive.boolean)
        assertTrue(entry["enabled"]!!.jsonPrimitive.boolean)
    }

    @Test
    fun an_unknown_device_id_is_omitted_rather_than_sent_blank() {

        val result = AppInventory.result(emptyList(), "", 12.0)

        assertNull(
            "a blank device id is not an identity; omit it",
            result["device_id"],
        )
    }

    @Test
    fun the_payload_count_matches_the_packages_it_carries() {

        val entries = AppInventory.collect(
            FakePackages(installed = listOf(app("com.a"), app("com.b")))
        )

        val result = AppInventory.result(entries, "d1", 12.0)

        assertEquals(packagesOf(result).size, countOf(result))
    }

    // ------------------------------------------------------------------
    // Privacy: the observation carries counts, never contents
    // ------------------------------------------------------------------

    @Test
    fun the_observation_payload_carries_no_package_names_or_labels() {

        // The observation is the part that travels into server-side
        // traces and observation records. An installed-app list is
        // privacy-sensitive even though reading it changes nothing, so
        // READ_ONLY must not be read as "safe to log".
        val entries = AppInventory.collect(
            FakePackages(
                launcher = listOf("com.whatsapp"),
                installed = listOf(
                    app("com.whatsapp", "WhatsApp", true, "2.24"),
                    app("com.secret.dating.app", "Private Label", true, "1.0"),
                ),
            )
        )

        val rendered = AppInventory.observationData(entries).toString()

        assertFalse("package names must not enter the observation",
            rendered.contains("whatsapp", ignoreCase = true))
        assertFalse("package names must not enter the observation",
            rendered.contains("com.secret", ignoreCase = true))
        assertFalse("app labels must not enter the observation",
            rendered.contains("Private Label", ignoreCase = true))

        // What it does carry: counts, so a reader can see how big the
        // answer was without seeing the answer.
        val data = AppInventory.observationData(entries)
        assertEquals(2, data["count"]!!.jsonPrimitive.int)
        assertEquals(1, data["launchable_count"]!!.jsonPrimitive.int)
    }

    @Test
    fun the_content_hash_changes_when_the_inventory_changes() {

        // Freshness has to be decidable without carrying the content:
        // two reads of an unchanged device hash the same, an install
        // does not. That is what lets a caller answer "is this the list
        // I already had?" from a hash instead of a guess.
        val before = AppInventory.collect(
            FakePackages(installed = listOf(app("com.a")))
        )
        val after = AppInventory.collect(
            FakePackages(installed = listOf(app("com.a"), app("com.b")))
        )

        assertEquals(
            AppInventory.contentHash(before),
            AppInventory.contentHash(
                AppInventory.collect(FakePackages(installed = listOf(app("com.a"))))
            ),
        )
        assertNotEquals(
            AppInventory.contentHash(before),
            AppInventory.contentHash(after),
        )
    }

    @Test
    fun the_content_hash_is_not_the_package_list_in_disguise() {

        val entries = AppInventory.collect(
            FakePackages(installed = listOf(app("com.whatsapp", "WhatsApp")))
        )

        val hash = AppInventory.contentHash(entries)

        assertFalse(hash.contains("whatsapp", ignoreCase = true))
        assertTrue("a sha-256 is 64 hex characters", hash.length == 64)
    }

    @Test
    fun a_disabled_flag_change_is_visible_to_the_freshness_hash() {

        // Enabled/disabled is part of current state, so a change to it
        // must not hash identically - otherwise a stale answer would
        // look current.
        val enabled = AppInventory.collect(
            FakePackages(installed = listOf(app("com.x", enabled = true)))
        )
        val disabled = AppInventory.collect(
            FakePackages(installed = listOf(app("com.x", enabled = false)))
        )

        assertNotEquals(
            AppInventory.contentHash(enabled),
            AppInventory.contentHash(disabled),
        )
    }

    @Test
    fun the_observation_kind_and_source_are_the_ones_the_server_reads() {

        // The server's evidence seam keys OBSERVATION evidence off these
        // two strings. If either drifts, a verified inventory silently
        // stops grounding "X is installed" - so they are pinned here as
        // a cross-boundary contract, not as an implementation detail.
        assertEquals("app_inventory", AppInventory.KIND)
        assertEquals("android.package_manager", AppInventory.SOURCE)
    }

    @Test
    fun no_inventory_cache_exists_to_go_stale() {

        // Two collects from two different platforms must not share a
        // result. There is deliberately no cache: an inventory is
        // current device state, and the only way a stale list cannot be
        // served as current is for there to be no stored list at all.
        val first = AppInventory.collect(
            FakePackages(installed = listOf(app("com.first")))
        )
        val second = AppInventory.collect(
            FakePackages(installed = listOf(app("com.second")))
        )

        assertEquals(listOf("com.first"), first.map { it.packageName })
        assertEquals(listOf("com.second"), second.map { it.packageName })
    }

    // ------------------------------------------------------------------
    // The permission the inventory does NOT need
    // ------------------------------------------------------------------

    @Test
    fun the_inventory_needs_no_new_permission_beyond_the_launcher_query() {

        // `minSdk 26` / `targetSdk 35`: package visibility filtering
        // applies (API 30+), and the manifest already answers it with the
        // MAIN/LAUNCHER `<queries>` block added for `open_app`. Phase 5A
        // adds no permission - and specifically not QUERY_ALL_PACKAGES,
        // which answers far more than "which apps are here" and needs a
        // policy declaration on Play.
        val manifest = java.io.File("src/main/AndroidManifest.xml")
            .takeIf { it.exists() }
            ?: java.io.File("app/src/main/AndroidManifest.xml")

        val text = manifest.readText()

        // The manifest's own `<queries>` comment mentions the string
        // "QUERY_ALL_PACKAGES" while explaining why it is deliberately
        // NOT used, so a plain substring search is the wrong check. What
        // matters - and what this pins - is that no `<uses-permission>`
        // element declares it; the app relies on the MAIN/LAUNCHER query.
        assertFalse(
            "Phase 5A must not introduce QUERY_ALL_PACKAGES",
            Regex("""<uses-permission[^>]*QUERY_ALL_PACKAGES""")
                .containsMatchIn(text),
        )
        assertTrue(
            "the inventory relies on the existing launcher query",
            "android.intent.category.LAUNCHER" in text,
        )
    }
}
