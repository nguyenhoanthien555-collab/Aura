package com.aura.companion.accessibility

import android.content.Intent
import android.content.pm.PackageManager
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * The installed-app inventory (Phase 5A) - the device half of
 * `android.list_apps` / `android.app_inventory`.
 *
 * WHY IT IS SHAPED LIKE THIS
 * --------------------------
 * A JVM unit test has no `PackageManager`, and mocking one would assert
 * that a mock was called. So the Android API lives behind
 * [PackageSource] - one narrow seam, the same trick [DeviceToolExecutor]
 * already plays for the dispatcher - and every rule about what the
 * inventory *means* lives in [AppInventory.collect], which is pure and
 * fully testable off-device.
 *
 * WHAT IT DOES NOT DO
 * -------------------
 * - It requests no new permission. Package visibility filtering applies
 *   on API 30+ (this app is `minSdk 26`, `targetSdk 35`), and the
 *   manifest already answers it with a `<queries>` MAIN/LAUNCHER block
 *   added for `open_app`. `QUERY_ALL_PACKAGES` is deliberately NOT
 *   declared: it answers far more than "which apps are here", and it
 *   needs a policy declaration on Play. The honest consequence is
 *   documented on [PackageSource.installedPackages].
 * - It invents nothing. A field the platform did not supply is absent
 *   from the payload rather than defaulted, because a fabricated
 *   `enabled: true` is indistinguishable downstream from an observed
 *   one, and the whole point of the evidence pipeline is that those two
 *   must never be confused.
 * - It caches nothing. Every call is a fresh enumeration stamped with a
 *   fresh `observed_at`, so a stale list can never be served as current
 *   device state.
 */

/**
 * One package as the platform handed it over, before any rules are
 * applied. Every field is nullable because every field can genuinely be
 * missing on a real device - `applicationInfo` is nullable on recent
 * API levels, and `versionName` has always been.
 */
data class RawPackage(
    val packageName: String?,
    val label: String? = null,
    val enabled: Boolean? = null,
    val versionName: String? = null,
)

/**
 * The two questions the inventory asks the platform.
 *
 * Split in two because they answer different things and are filtered
 * differently: the launcher query is what the manifest's `<queries>`
 * block explicitly grants, and it is the only reliable source of
 * "can this be opened".
 */
interface PackageSource {

    /**
     * Packages with a MAIN/LAUNCHER entry - exactly what the manifest
     * declares an interest in, and exactly what `open_app` can launch.
     */
    fun launcherPackages(): List<String>

    /**
     * Every package this app is allowed to see, launchable or not.
     *
     * On API 30+ without `QUERY_ALL_PACKAGES` this is filtered to the
     * visible set, so a package with no launcher entry may simply not
     * appear. That is a smaller answer, never a wrong one: the inventory
     * reports what the device could see, and the absence of an entry is
     * never reported as "not installed".
     */
    fun installedPackages(): List<RawPackage>
}

/** The real thing, over `PackageManager`. Not reachable from a JVM test. */
class PlatformPackageSource(
    private val packages: PackageManager,
) : PackageSource {

    @Suppress("DEPRECATION")
    override fun launcherPackages(): List<String> {
        val intent = Intent(Intent.ACTION_MAIN)
            .addCategory(Intent.CATEGORY_LAUNCHER)

        return packages.queryIntentActivities(intent, 0)
            .mapNotNull { it.activityInfo?.packageName }
    }

    @Suppress("DEPRECATION")
    override fun installedPackages(): List<RawPackage> =
        packages.getInstalledPackages(0).map { info ->
            val application = info.applicationInfo

            RawPackage(
                packageName = info.packageName,
                // `getApplicationLabel` resolves a resource, which can
                // fail for a half-installed or corrupt package. A label
                // is a convenience; losing one must not lose the entry.
                label = application?.let {
                    runCatching {
                        packages.getApplicationLabel(it).toString()
                    }.getOrNull()
                },
                enabled = application?.enabled,
                versionName = info.versionName,
            )
        }
}

object AppInventory {

    /** The observation kind and source the server's evidence seam reads. */
    const val KIND = "app_inventory"
    const val SOURCE = "android.package_manager"

    /**
     * One installed app, after the rules.
     *
     * `launchable` is the one field that is always known: the launcher
     * query either listed the package or it did not. `label`, `enabled`
     * and `versionName` stay null when the platform did not supply them,
     * and null means omitted from the wire payload - never guessed.
     */
    data class Entry(
        val packageName: String,
        val label: String? = null,
        val launchable: Boolean = false,
        val enabled: Boolean? = null,
        val versionName: String? = null,
    )

    /**
     * Enumerate, merge, order. Pure - no Android, no clock, no I/O.
     *
     * Deterministic on three counts, because a non-deterministic
     * inventory would make freshness undecidable: entries are ordered by
     * package name, a package seen by both queries produces exactly one
     * entry, and an entry the platform reported twice stays twice (the
     * transport reports what the device reported; deciding whether a
     * duplicate is a device oddity is not the transport's job).
     *
     * A package with no usable name is dropped, not repaired: an entry
     * that cannot be identified cannot be evidence about anything.
     */
    fun collect(source: PackageSource): List<Entry> {

        val launchable: Set<String> = source.launcherPackages()
            .mapNotNull { it.usable() }
            .toSet()

        val entries = mutableListOf<Entry>()
        val described = mutableSetOf<String>()

        source.installedPackages().forEach { raw ->
            val name = raw.packageName?.usable() ?: return@forEach

            described += name

            entries += Entry(
                packageName = name,
                label = raw.label?.usable(),
                launchable = name in launchable,
                enabled = raw.enabled,
                versionName = raw.versionName?.usable(),
            )
        }

        // Launchable but not described: the launcher query saw it and the
        // package query did not. Reported as the one thing that is known
        // about it rather than dropped, and with nothing else invented.
        (launchable - described).forEach { name ->
            entries += Entry(packageName = name, launchable = true)
        }

        return entries.sortedBy { it.packageName }
    }

    /**
     * The wire payload for a successful `android.list_apps`.
     *
     * Snake-case keys and epoch-second timestamps, matching every other
     * device report in this package. `device_id` is omitted when the
     * handset has not established one yet, rather than sent blank.
     */
    fun result(
        entries: List<Entry>,
        deviceId: String,
        observedAt: Double,
    ): JsonObject = buildJsonObject {
        put("packages", packagesJson(entries))
        put("count", entries.size)
        put("observed_at", observedAt)
        if (deviceId.isNotBlank()) {
            put("device_id", deviceId)
        }
        put("source", SOURCE)
    }

    fun packagesJson(entries: List<Entry>): JsonArray = JsonArray(
        entries.map { entry ->
            buildJsonObject {
                put("package", entry.packageName)
                entry.label?.let { put("label", it) }
                put("launchable", entry.launchable)
                entry.enabled?.let { put("enabled", it) }
                entry.versionName?.let { put("version_name", it) }
            }
        }
    )

    /**
     * The observation metadata an inventory read carries - COUNTS ONLY.
     *
     * No package names and no labels, ever. The observation payload is
     * the part that travels into server-side traces and observation
     * records, and an installed-app list is privacy-sensitive even
     * though reading it changes nothing. The content hash below is what
     * keeps freshness decidable without carrying the content.
     */
    fun observationData(entries: List<Entry>): JsonObject = buildJsonObject {
        put("count", entries.size)
        put("launchable_count", entries.count { it.launchable })
        put("source", SOURCE)
    }

    /**
     * A fingerprint of the inventory, so "is this the list I already
     * had?" is a comparison instead of a guess. Hashed, not carried:
     * a hash of package names is not a list of package names.
     */
    fun contentHash(entries: List<Entry>): String = ObservationIds.hashOf(
        entries.joinToString("|") { entry ->
            "${entry.packageName}:${entry.launchable}:${entry.enabled}"
        }
    )

    /** Blank, whitespace-only and absent all mean the same: no value. */
    private fun String.usable(): String? = trim().takeIf { it.isNotEmpty() }
}
