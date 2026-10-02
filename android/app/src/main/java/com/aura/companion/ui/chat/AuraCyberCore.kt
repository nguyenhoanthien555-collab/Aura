package com.aura.companion.ui.chat

import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aura.companion.data.local.PhoneTelemetry
import com.aura.companion.data.remote.HostTelemetryDto
import com.aura.companion.ui.theme.AuraIcons
import com.aura.companion.ui.theme.auraGlassBlur

/**
 * Futuristic Cyber-Core HUD Capsule displayed at the top of ChatScreen.
 *
 * Provides real-time dual-device telemetry (Host PC & Phone Handset),
 * animated ambient pulsing core, and expands into a full hardware HUD sheet.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun AuraCyberCoreCapsule(
    state: ChatUiState,
    onRefreshTelemetry: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var showSheet by remember { mutableStateOf(false) }

    val infiniteTransition = rememberInfiniteTransition(label = "cyber_core_pulse")
    val pulseScale by infiniteTransition.animateFloat(
        initialValue = 0.95f,
        targetValue = 1.05f,
        animationSpec = infiniteRepeatable(
            animation = tween(2200, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "pulse_scale",
    )
    val glowAlpha by infiniteTransition.animateFloat(
        initialValue = 0.4f,
        targetValue = 0.85f,
        animationSpec = infiniteRepeatable(
            animation = tween(2200, easing = LinearEasing),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "glow_alpha",
    )

    val isConnected = state.connection is ConnectionState.Connected
    val isConnecting = state.connection is ConnectionState.Connecting || state.connection is ConnectionState.WakingUp

    val statusColor = when {
        isConnected -> Color(0xFF10B981) // Cyber Emerald
        isConnecting -> Color(0xFFF59E0B) // Cyber Amber
        else -> Color(0xFFEF4444) // Cyber Red
    }

    val capsuleShape = RoundedCornerShape(16.dp)
    val borderGradient = Brush.horizontalGradient(
        colors = listOf(
            Color(0xFF8B5CF6).copy(alpha = 0.35f), // Neon Violet
            Color(0xFF06B6D4).copy(alpha = 0.35f), // Neon Cyan
        )
    )

    Box(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 14.dp, vertical = 3.dp)
            .clip(capsuleShape)
            .border(0.75.dp, borderGradient, capsuleShape)
            .auraGlassBlur(
                shape = capsuleShape,
                tint = MaterialTheme.colorScheme.surface.copy(alpha = 0.60f),
            )
            .clickable { showSheet = true }
            .padding(horizontal = 10.dp, vertical = 5.dp)
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth(),
        ) {
            // Mini Pulsing Cyber Core
            Box(
                modifier = Modifier
                    .size(24.dp)
                    .scale(pulseScale),
                contentAlignment = Alignment.Center,
            ) {
                Box(
                    modifier = Modifier
                        .size(22.dp)
                        .clip(CircleShape)
                        .background(
                            Brush.radialGradient(
                                colors = listOf(
                                    Color(0xFF8B5CF6).copy(alpha = glowAlpha * 0.7f),
                                    Color(0xFF06B6D4).copy(alpha = glowAlpha * 0.3f),
                                    Color.Transparent,
                                )
                            )
                        )
                )
                Surface(
                    shape = RoundedCornerShape(7.dp),
                    color = Color(0xFF1E1B4B).copy(alpha = 0.9f),
                    border = androidx.compose.foundation.BorderStroke(0.75.dp, Color(0xFF8B5CF6).copy(alpha = 0.8f)),
                    modifier = Modifier.size(18.dp),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(
                            imageVector = AuraIcons.Brain,
                            contentDescription = "Aura Core",
                            tint = Color(0xFFC084FC),
                            modifier = Modifier.size(11.dp),
                        )
                    }
                }
            }

            Spacer(Modifier.width(8.dp))

            // Brand & Status Dot
            Text(
                text = "AURA",
                style = MaterialTheme.typography.labelSmall.copy(
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 1.0.sp,
                    fontSize = 11.sp,
                ),
                color = Color(0xFFE0E7FF),
            )

            Spacer(Modifier.width(6.dp))

            Box(
                modifier = Modifier
                    .size(6.dp)
                    .clip(CircleShape)
                    .background(statusColor)
            )

            Spacer(Modifier.width(4.dp))

            when (val c = state.connection) {
                is ConnectionState.Connected -> {
                    val provider = c.provider
                    val providerIcon = when {
                        provider.contains("chatgpt", ignoreCase = true) -> AuraIcons.ChatGPT
                        provider.contains("gemini", ignoreCase = true) -> AuraIcons.Gemini
                        provider.contains("openrouter", ignoreCase = true) -> AuraIcons.OpenRouter
                        provider.contains("claude", ignoreCase = true) -> AuraIcons.Claude
                        else -> AuraIcons.Brain
                    }
                    val providerTint = when {
                        provider.contains("chatgpt", ignoreCase = true) -> Color(0xFF10A37F)
                        provider.contains("gemini", ignoreCase = true) -> Color(0xFF38BDF8)
                        provider.contains("openrouter", ignoreCase = true) -> Color(0xFF818CF8)
                        provider.contains("claude", ignoreCase = true) -> Color(0xFFD97706)
                        else -> Color(0xFFC084FC)
                    }
                    if (state.isSending) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.weight(1f, fill = false),
                        ) {
                            Icon(
                                imageVector = providerIcon,
                                contentDescription = provider,
                                tint = providerTint,
                                modifier = Modifier.size(13.dp),
                            )
                            Spacer(Modifier.width(4.dp))
                            Text(
                                text = "Đang nghĩ...",
                                style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    } else {
                        Icon(
                            imageVector = providerIcon,
                            contentDescription = provider,
                            tint = providerTint,
                            modifier = Modifier.size(13.dp),
                        )
                    }
                }
                is ConnectionState.Connecting -> {
                    Text(
                        text = "Đang kết nối...",
                        style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                }
                is ConnectionState.WakingUp -> {
                    Text(
                        text = "Đang đánh thức...",
                        style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                }
                is ConnectionState.OnDevice -> {
                    Text(
                        text = "On-Device",
                        style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                }
                is ConnectionState.Unavailable -> {
                    Text(
                        text = "Mất kết nối",
                        style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                }
                is ConnectionState.Unknown -> {
                    Text(
                        text = "Chưa khởi tạo",
                        style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f, fill = false),
                    )
                }
            }

            Spacer(Modifier.weight(1f))

            // Right-side Compact Telemetry Badges
            val host = state.hostTelemetry
            val isWindows = host?.os?.contains("Windows", ignoreCase = true) == true
            val hostIcon = if (isWindows) AuraIcons.Laptop else AuraIcons.Cloud
            val hostLabel = if (state.pingMs > 0) "${state.pingMs}ms" else if (isWindows) "PC" else "Cloud"
            CyberMiniBadge(
                icon = hostIcon,
                text = hostLabel,
                tint = Color(0xFF38BDF8),
            )

            Spacer(Modifier.width(5.dp))

            val phone = state.phoneTelemetry
            val phoneLabel = if (phone != null) "${phone.batteryPercent}%${if (phone.isCharging) "⚡" else ""}" else "Phone"
            CyberMiniBadge(
                icon = AuraIcons.DeviceMobile,
                text = phoneLabel,
                tint = Color(0xFF34D399),
            )

            Spacer(Modifier.width(4.dp))

            Icon(
                imageVector = AuraIcons.ChevronRight,
                contentDescription = "Chi tiết Telemetry",
                tint = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.5f),
                modifier = Modifier.size(14.dp),
            )
        }
    }

    if (showSheet) {
        DualDeviceTelemetrySheet(
            state = state,
            onDismiss = { showSheet = false },
            onRefresh = onRefreshTelemetry,
        )
    }
}

/**
 * Micro cyber badge with icon and label.
 */
@Composable
private fun CyberMiniBadge(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    text: String,
    tint: Color,
) {
    Surface(
        shape = RoundedCornerShape(8.dp),
        color = tint.copy(alpha = 0.12f),
        border = androidx.compose.foundation.BorderStroke(0.5.dp, tint.copy(alpha = 0.35f)),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = tint,
                modifier = Modifier.size(11.dp),
            )
            Spacer(Modifier.width(4.dp))
            Text(
                text = text,
                style = MaterialTheme.typography.labelSmall.copy(
                    fontSize = 10.sp,
                    fontWeight = FontWeight.Medium,
                    fontFamily = FontFamily.Monospace,
                ),
                color = tint,
                maxLines = 1,
            )
        }
    }
}

/**
 * Bottom Sheet presenting comprehensive dual-device telemetry for Host PC & Phone Handset.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun DualDeviceTelemetrySheet(
    state: ChatUiState,
    onDismiss: () -> Unit,
    onRefresh: () -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = Color(0xFF0F0E17),
        dragHandle = {
            Box(
                modifier = Modifier
                    .padding(vertical = 10.dp)
                    .width(40.dp)
                    .height(4.dp)
                    .clip(CircleShape)
                    .background(Color(0xFF475569))
            )
        },
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp)
                .padding(bottom = 32.dp)
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            // Sheet Title & Refresh Button
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.weight(1f),
                ) {
                    Icon(
                        imageVector = AuraIcons.MonitorHeart,
                        contentDescription = null,
                        tint = Color(0xFF38BDF8),
                        modifier = Modifier.size(20.dp),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = "TRẠNG THÁI HỆ THỐNG",
                        style = MaterialTheme.typography.titleSmall.copy(
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
                            letterSpacing = 0.5.sp,
                        ),
                        color = Color.White,
                        maxLines = 1,
                    )
                }

                Button(
                    onClick = onRefresh,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color(0xFF1E293B),
                        contentColor = Color(0xFF38BDF8),
                    ),
                    contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                    shape = RoundedCornerShape(8.dp),
                ) {
                    Icon(
                        imageVector = AuraIcons.Sync,
                        contentDescription = "Làm mới",
                        modifier = Modifier.size(14.dp),
                    )
                    Spacer(Modifier.width(4.dp))
                    Text("Làm mới", style = MaterialTheme.typography.labelSmall)
                }
            }

            // 1. Host Laptop PC Hardware Card
            HostHardwareCard(
                host = state.hostTelemetry,
                pingMs = state.pingMs,
            )

            // 2. Client Handset Device Card
            PhoneHardwareCard(
                phone = state.phoneTelemetry,
                pingMs = state.pingMs,
            )

            // 3. Aura Engine & Intelligence Card
            AuraEngineCard(
                connection = state.connection,
                pingMs = state.pingMs,
            )
        }
    }
}

/**
 * Host Hardware Card (Render Cloud Server or Host PC/Laptop).
 */
@Composable
fun HostHardwareCard(
    host: HostTelemetryDto?,
    pingMs: Long,
) {
    val cardShape = RoundedCornerShape(16.dp)
    val isWindows = host?.os?.contains("Windows", ignoreCase = true) == true
    val isLinux = host?.os?.contains("Linux", ignoreCase = true) == true

    val hostTitle = when {
        host != null && host.model.isNotBlank() && host.model != "Standard PC" -> host.model
        isWindows -> "MÁY TÍNH LAPTOP (HOST)"
        isLinux -> "MÁY CHỦ CLOUD (RENDER.COM)"
        else -> "MÁY CHỦ AURA"
    }

    val hostBadge = when {
        host != null && host.os.isNotBlank() -> host.os
        isWindows -> "Windows PC"
        else -> "Render Cloud / Linux"
    }

    val hostIcon = when {
        isWindows -> AuraIcons.Laptop
        else -> AuraIcons.Cloud
    }

    Surface(
        shape = cardShape,
        color = Color(0xFF131224),
        border = androidx.compose.foundation.BorderStroke(1.dp, Color(0xFF38BDF8).copy(alpha = 0.3f)),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // Header
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.weight(1f, fill = false),
                ) {
                    Icon(
                        imageVector = hostIcon,
                        contentDescription = null,
                        tint = Color(0xFF38BDF8),
                        modifier = Modifier.size(20.dp),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = hostTitle,
                        style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold),
                        color = Color.White,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }

                Spacer(Modifier.width(8.dp))

                Surface(
                    shape = RoundedCornerShape(6.dp),
                    color = Color(0xFF0369A1).copy(alpha = 0.25f),
                    border = androidx.compose.foundation.BorderStroke(0.5.dp, Color(0xFF38BDF8)),
                ) {
                    Text(
                        text = hostBadge,
                        style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                        color = Color(0xFF7DD3FC),
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                    )
                }
            }

            if (host == null) {
                Text(
                    text = "Đang kết nối telemetry máy chủ Aura (Render Cloud / Host)...",
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFF94A3B8),
                )
            } else {
                // CPU Gauge
                MetricGaugeBar(
                    label = "CPU (${if (host.cpuName.isNotBlank()) host.cpuName else "${host.cpuCores} cores"})",
                    value = "${host.cpuPercent.toInt()}%",
                    progress = (host.cpuPercent / 100.0).toFloat().coerceIn(0f, 1f),
                    barColor = Color(0xFF38BDF8),
                )

                // RAM Gauge
                val ramUsed = String.format("%.1f", (host.ramTotalGb - host.ramAvailableGb).coerceAtLeast(0.0))
                val ramTotal = String.format("%.1f", host.ramTotalGb)
                MetricGaugeBar(
                    label = "Bộ nhớ RAM ($ramUsed GB / $ramTotal GB)",
                    value = "${host.ramUsedPercent.toInt()}%",
                    progress = (host.ramUsedPercent / 100.0).toFloat().coerceIn(0f, 1f),
                    barColor = Color(0xFFA855F7),
                )

                // Storage Gauge
                val storageUsedGb = String.format("%.1f", (host.storageTotalGb - host.storageFreeGb).coerceAtLeast(0.0))
                val storageTotalGb = String.format("%.1f", host.storageTotalGb)
                MetricGaugeBar(
                    label = "Ổ đĩa lưu trữ ($storageUsedGb GB / $storageTotalGb GB)",
                    value = "${host.storageUsedPercent.toInt()}%",
                    progress = (host.storageUsedPercent / 100.0).toFloat().coerceIn(0f, 1f),
                    barColor = Color(0xFF10B981),
                )

                // GPUs or Cloud Compute
                if (host.gpus.isNotEmpty()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = AuraIcons.Cpu,
                            contentDescription = null,
                            tint = Color(0xFFF472B6),
                            modifier = Modifier.size(14.dp),
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            text = host.gpus.joinToString(separator = ", "),
                            style = MaterialTheme.typography.bodySmall.copy(fontSize = 11.sp),
                            color = Color(0xFFCBD5E1),
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                } else {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = AuraIcons.Cpu,
                            contentDescription = null,
                            tint = Color(0xFF818CF8),
                            modifier = Modifier.size(14.dp),
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            text = "Điện toán: Container Cloud vCPU",
                            style = MaterialTheme.typography.bodySmall.copy(fontSize = 11.sp),
                            color = Color(0xFFCBD5E1),
                        )
                    }
                }

                // Battery / Power
                if (host.battery != null) {
                    val bat = host.battery
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = AuraIcons.BatteryCharging,
                            contentDescription = null,
                            tint = Color(0xFFFBBF24),
                            modifier = Modifier.size(14.dp),
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            text = "Pin thiết bị: ${bat.percent.toInt()}% (${if (bat.powerPlugged) "Đang cắm nguồn" else "Đang dùng pin"})",
                            style = MaterialTheme.typography.bodySmall.copy(fontSize = 11.sp),
                            color = Color(0xFFCBD5E1),
                        )
                    }
                } else {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = AuraIcons.Bolt,
                            contentDescription = null,
                            tint = Color(0xFF38BDF8),
                            modifier = Modifier.size(14.dp),
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            text = "Nguồn điện: Datacenter Server (Hoạt động 24/7)",
                            style = MaterialTheme.typography.bodySmall.copy(fontSize = 11.sp),
                            color = Color(0xFFCBD5E1),
                        )
                    }
                }
            }
        }
    }
}

/**
 * Phone Client Hardware Card (Oppo CPH2251).
 */
@Composable
fun PhoneHardwareCard(
    phone: PhoneTelemetry?,
    pingMs: Long,
) {
    val cardShape = RoundedCornerShape(16.dp)

    Surface(
        shape = cardShape,
        color = Color(0xFF131224),
        border = androidx.compose.foundation.BorderStroke(1.dp, Color(0xFF34D399).copy(alpha = 0.3f)),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            // Header
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        imageVector = AuraIcons.DeviceMobile,
                        contentDescription = null,
                        tint = Color(0xFF34D399),
                        modifier = Modifier.size(20.dp),
                    )
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = if (phone != null) "${phone.manufacturer} ${phone.model}" else "ĐIỆN THOẠI ANDROID",
                        style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold),
                        color = Color.White,
                    )
                }

                Surface(
                    shape = RoundedCornerShape(6.dp),
                    color = Color(0xFF065F46).copy(alpha = 0.25f),
                    border = androidx.compose.foundation.BorderStroke(0.5.dp, Color(0xFF34D399)),
                ) {
                    Text(
                        text = phone?.androidVersion ?: "Android 13",
                        style = MaterialTheme.typography.labelSmall.copy(fontSize = 10.sp),
                        color = Color(0xFF6EE7B7),
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                    )
                }
            }

            if (phone == null) {
                Text(
                    text = "Đang thu thập thông số điện thoại...",
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFF94A3B8),
                )
            } else {
                // Battery Gauge
                MetricGaugeBar(
                    label = "Mức pin điện thoại (${if (phone.isCharging) "Đang sạc nhanh ⚡" else "Đang sử dụng"})",
                    value = "${phone.batteryPercent}%",
                    progress = (phone.batteryPercent / 100f).coerceIn(0f, 1f),
                    barColor = if (phone.batteryPercent > 20) Color(0xFF34D399) else Color(0xFFEF4444),
                )

                // Network & Ping Row
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = AuraIcons.WifiTethering,
                            contentDescription = null,
                            tint = Color(0xFF38BDF8),
                            modifier = Modifier.size(14.dp),
                        )
                        Spacer(Modifier.width(6.dp))
                        Text(
                            text = "Kết nối: ${phone.networkType}",
                            style = MaterialTheme.typography.bodySmall,
                            color = Color(0xFFCBD5E1),
                        )
                    }

                    // Ping badge
                    val pingColor = when {
                        pingMs < 60 -> Color(0xFF34D399)
                        pingMs < 200 -> Color(0xFFFBBF24)
                        else -> Color(0xFFEF4444)
                    }
                    Text(
                        text = "Độ trễ: ${pingMs}ms",
                        style = MaterialTheme.typography.labelSmall.copy(
                            fontFamily = FontFamily.Monospace,
                            fontWeight = FontWeight.Bold,
                        ),
                        color = pingColor,
                    )
                }
            }
        }
    }
}

/**
 * Aura AI Engine and Knowledge Card.
 */
@Composable
fun AuraEngineCard(
    connection: ConnectionState,
    pingMs: Long,
) {
    val cardShape = RoundedCornerShape(16.dp)

    Surface(
        shape = cardShape,
        color = Color(0xFF131224),
        border = androidx.compose.foundation.BorderStroke(1.dp, Color(0xFFA855F7).copy(alpha = 0.3f)),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    imageVector = AuraIcons.Spark,
                    contentDescription = null,
                    tint = Color(0xFFA855F7),
                    modifier = Modifier.size(20.dp),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    text = "AURA INTELLIGENCE & TRI THỨC",
                    style = MaterialTheme.typography.titleSmall.copy(fontWeight = FontWeight.Bold),
                    color = Color.White,
                )
            }

            val provider = when (connection) {
                is ConnectionState.Connected -> connection.provider
                else -> "Cloud AI"
            }
            Text(
                text = "Mô hình ngôn ngữ: $provider",
                style = MaterialTheme.typography.bodySmall,
                color = Color(0xFFCBD5E1),
            )
            Text(
                text = "Bộ nhớ thực thể: Kết nối bền vững (Durable SQLite Graph)",
                style = MaterialTheme.typography.bodySmall,
                color = Color(0xFFCBD5E1),
            )
        }
    }
}

/**
 * Metric Progress Gauge Bar.
 */
@Composable
fun MetricGaugeBar(
    label: String,
    value: String,
    progress: Float,
    barColor: Color,
) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                text = label,
                style = MaterialTheme.typography.bodySmall.copy(fontSize = 11.sp),
                color = Color(0xFF94A3B8),
            )
            Text(
                text = value,
                style = MaterialTheme.typography.labelSmall.copy(
                    fontFamily = FontFamily.Monospace,
                    fontWeight = FontWeight.Bold,
                ),
                color = barColor,
            )
        }
        LinearProgressIndicator(
            progress = { progress },
            modifier = Modifier
                .fillMaxWidth()
                .height(6.dp)
                .clip(RoundedCornerShape(3.dp)),
            color = barColor,
            trackColor = Color(0xFF1E293B),
        )
    }
}
