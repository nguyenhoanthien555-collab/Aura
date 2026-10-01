package com.aura.companion.data.local

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Build

data class PhoneTelemetry(
    val model: String = Build.MODEL,
    val manufacturer: String = Build.MANUFACTURER,
    val androidVersion: String = "Android ${Build.VERSION.RELEASE}",
    val batteryPercent: Int = 100,
    val isCharging: Boolean = false,
    val networkType: String = "WiFi",
    val pingMs: Long = 0L,
)

object DeviceTelemetryProbe {
    fun sample(context: Context, pingMs: Long = 0L): PhoneTelemetry {
        var batteryPct = 100
        var isCharging = false

        runCatching {
            val batteryStatus: Intent? = IntentFilter(Intent.ACTION_BATTERY_CHANGED).let { filter ->
                context.registerReceiver(null, filter)
            }
            val level: Int = batteryStatus?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
            val scale: Int = batteryStatus?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
            if (level >= 0 && scale > 0) {
                batteryPct = ((level / scale.toFloat()) * 100).toInt()
            }
            val status: Int = batteryStatus?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
            isCharging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
                    status == BatteryManager.BATTERY_STATUS_FULL
        }

        var netType = "Unknown"
        runCatching {
            val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            val network = cm?.activeNetwork
            val caps = cm?.getNetworkCapabilities(network)
            netType = when {
                caps == null -> "Offline"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "WiFi"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "Cellular 5G"
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "Ethernet"
                else -> "Online"
            }
        }

        return PhoneTelemetry(
            model = Build.MODEL,
            manufacturer = Build.MANUFACTURER,
            androidVersion = "Android ${Build.VERSION.RELEASE}",
            batteryPercent = batteryPct,
            isCharging = isCharging,
            networkType = netType,
            pingMs = pingMs,
        )
    }
}
