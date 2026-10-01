package com.aura.companion.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class TelemetryDto(
    @SerialName("host") val host: HostTelemetryDto = HostTelemetryDto(),
    @SerialName("aura") val aura: AuraTelemetryDto = AuraTelemetryDto(),
)

@Serializable
data class HostTelemetryDto(
    @SerialName("hostname") val hostname: String = "",
    @SerialName("model") val model: String = "",
    @SerialName("os") val os: String = "",
    @SerialName("cpu_name") val cpuName: String = "",
    @SerialName("cpu_cores") val cpuCores: Int = 0,
    @SerialName("cpu_percent") val cpuPercent: Double = 0.0,
    @SerialName("ram_total_gb") val ramTotalGb: Double = 0.0,
    @SerialName("ram_available_gb") val ramAvailableGb: Double = 0.0,
    @SerialName("ram_used_percent") val ramUsedPercent: Double = 0.0,
    @SerialName("storage_total_gb") val storageTotalGb: Double = 0.0,
    @SerialName("storage_free_gb") val storageFreeGb: Double = 0.0,
    @SerialName("storage_used_percent") val storageUsedPercent: Double = 0.0,
    @SerialName("gpus") val gpus: List<String> = emptyList(),
    @SerialName("battery") val battery: HostBatteryDto? = null,
    @SerialName("uptime_seconds") val uptimeSeconds: Double = 0.0,
)

@Serializable
data class HostBatteryDto(
    @SerialName("percent") val percent: Double = 0.0,
    @SerialName("power_plugged") val powerPlugged: Boolean = false,
    @SerialName("secs_left") val secsLeft: Long = -1,
)

@Serializable
data class AuraTelemetryDto(
    @SerialName("version") val version: String = "",
    @SerialName("status") val status: String = "",
    @SerialName("llm_provider") val llmProvider: String = "",
    @SerialName("memory_connected") val memoryConnected: Boolean = false,
    @SerialName("vision_enabled") val visionEnabled: Boolean = false,
)
