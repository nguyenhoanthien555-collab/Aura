package com.aura.companion.alarm.ui

import android.app.KeyguardManager
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.Crossfade
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aura.companion.MainActivity
import com.aura.companion.alarm.audio.AlarmAudioPlayer
import com.aura.companion.alarm.data.AlarmStore
import com.aura.companion.alarm.receiver.AuraAlarmReceiver
import com.aura.companion.alarm.service.AlarmScheduler
import com.aura.companion.ui.theme.AuraIcons
import com.aura.companion.ui.theme.AuraNeonCyan
import com.aura.companion.ui.theme.AuraNeonPink
import com.aura.companion.ui.theme.AuraTheme
import kotlinx.coroutines.delay
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Full-screen Lockscreen Activity displayed when an Aura alarm fires.
 * Wakes the device, turns the screen on, plays escalation audio, and transitions to Morning Briefing on dismiss.
 */
class AuraAlarmActivity : ComponentActivity() {

    private var audioPlayer: AlarmAudioPlayer? = null
    private var alarmId: String = ""
    private var alarmLabel: String = "Báo thức Aura"

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        alarmId = intent.getStringExtra(AuraAlarmReceiver.EXTRA_ALARM_ID) ?: ""
        alarmLabel = intent.getStringExtra(AuraAlarmReceiver.EXTRA_ALARM_LABEL) ?: "Báo thức Aura"

        configureLockscreenWakeup()

        audioPlayer = AlarmAudioPlayer(this).apply {
            start(stage1TimeoutSeconds = 180) // 3-minute escalation ladder
        }

        setContent {
            AuraTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = Color(0xFF030712) // Deep cyber void
                ) {
                    AlarmActivityScreen(
                        alarmLabel = alarmLabel,
                        onStopAlarm = {
                            handleStopAlarm()
                        },
                        onSnooze = {
                            handleSnooze()
                        },
                        onOpenChat = {
                            handleOpenChat()
                        }
                    )
                }
            }
        }
    }

    private fun configureLockscreenWakeup() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
            val km = getSystemService(KEYGUARD_SERVICE) as? KeyguardManager
            km?.requestDismissKeyguard(this, null)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON or
                WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
            )
        }
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
    }

    private fun handleStopAlarm() {
        audioPlayer?.stop()
        val nm = getSystemService(NOTIFICATION_SERVICE) as? NotificationManager
        nm?.cancel(alarmId.hashCode())

        // Update alarm state if one-shot
        val store = AlarmStore(this)
        val alarm = store.get(alarmId)
        if (alarm != null) {
            if (alarm.repeatDays.isEmpty()) {
                store.toggle(alarmId, enabled = false)
            } else {
                AlarmScheduler(this, store).schedule(alarm)
            }
        }
    }

    private fun handleSnooze() {
        audioPlayer?.stop()
        val nm = getSystemService(NOTIFICATION_SERVICE) as? NotificationManager
        nm?.cancel(alarmId.hashCode())

        AlarmScheduler(this, AlarmStore(this)).snooze(alarmId, minutes = 5)
        finish()
    }

    private fun handleOpenChat() {
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        startActivity(intent)
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        audioPlayer?.stop()
    }
}

@Composable
private fun AlarmActivityScreen(
    alarmLabel: String,
    onStopAlarm: () -> Unit,
    onSnooze: () -> Unit,
    onOpenChat: () -> Unit
) {
    var isBriefing by remember { mutableStateOf(false) }

    Crossfade(targetState = isBriefing, label = "AlarmCrossfade") { briefingState ->
        if (!briefingState) {
            CyberAlarmRingingView(
                alarmLabel = alarmLabel,
                onStop = {
                    onStopAlarm()
                    isBriefing = true
                },
                onSnooze = onSnooze
            )
        } else {
            CyberMorningBriefingView(
                onOpenChat = onOpenChat
            )
        }
    }
}

@Composable
private fun CyberAlarmRingingView(
    alarmLabel: String,
    onStop: () -> Unit,
    onSnooze: () -> Unit
) {
    var currentTimeText by remember { mutableStateOf("") }
    var currentDateText by remember { mutableStateOf("") }

    LaunchedEffect(Unit) {
        val timeFormat = SimpleDateFormat("HH:mm", Locale.getDefault())
        val dateFormat = SimpleDateFormat("EEEE, dd MMMM", Locale("vi", "VN"))
        while (true) {
            val now = Date()
            currentTimeText = timeFormat.format(now)
            currentDateText = dateFormat.format(now).replaceFirstChar { it.uppercase() }
            delay(1000)
        }
    }

    val transition = rememberInfiniteTransition(label = "RadarPulse")
    val pulseRatio by transition.animateFloat(
        initialValue = 0.85f,
        targetValue = 1.35f,
        animationSpec = infiniteRepeatable(
            animation = tween(2000, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Restart
        ),
        label = "PulseRatio"
    )
    val alphaRatio by transition.animateFloat(
        initialValue = 0.8f,
        targetValue = 0.0f,
        animationSpec = infiniteRepeatable(
            animation = tween(2000, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Restart
        ),
        label = "AlphaRatio"
    )

    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.radialGradient(
                    colors = listOf(
                        AuraNeonCyan.copy(alpha = 0.12f),
                        Color(0xFF030712)
                    )
                )
            )
            .padding(24.dp),
        contentAlignment = Alignment.Center
    ) {
        // Background radar pulse
        Canvas(modifier = Modifier.size(340.dp)) {
            val radius = (size.minDimension / 2f) * pulseRatio
            drawCircle(
                color = AuraNeonCyan.copy(alpha = alphaRatio * 0.4f),
                radius = radius,
                style = Stroke(width = 3.dp.toPx())
            )
        }

        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.SpaceBetween,
            modifier = Modifier.fillMaxSize().padding(vertical = 32.dp)
        ) {
            // Header: Aura Identity Badge
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .clip(RoundedCornerShape(32.dp))
                    .background(Color(0xFF111827).copy(alpha = 0.8f))
                    .border(1.dp, AuraNeonCyan.copy(alpha = 0.4f), RoundedCornerShape(32.dp))
                    .padding(horizontal = 16.dp, vertical = 8.dp)
            ) {
                Icon(
                    imageVector = AuraIcons.Face,
                    contentDescription = null,
                    tint = AuraNeonCyan,
                    modifier = Modifier.size(20.dp)
                )
                Spacer(modifier = Modifier.width(8.dp))
                Text(
                    text = "AURA ALARM CORE",
                    color = Color.White,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    letterSpacing = 1.sp
                )
            }

            // Central Ring Clock HUD
            Column(
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    text = currentTimeText,
                    fontSize = 72.sp,
                    fontWeight = FontWeight.ExtraBold,
                    fontFamily = FontFamily.Monospace,
                    color = Color.White,
                    letterSpacing = (-2).sp
                )

                Text(
                    text = currentDateText,
                    fontSize = 15.sp,
                    color = Color(0xFF9CA3AF),
                    fontWeight = FontWeight.Medium
                )

                Spacer(modifier = Modifier.height(20.dp))

                // Alarm Label Box
                Box(
                    modifier = Modifier
                        .clip(RoundedCornerShape(12.dp))
                        .background(Color(0xFF1F2937).copy(alpha = 0.6f))
                        .border(1.dp, Color(0xFF374151), RoundedCornerShape(12.dp))
                        .padding(horizontal = 20.dp, vertical = 10.dp)
                ) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = AuraIcons.Alarm,
                            contentDescription = null,
                            tint = AuraNeonPink,
                            modifier = Modifier.size(18.dp)
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = alarmLabel,
                            color = Color(0xFFE5E7EB),
                            fontSize = 16.sp,
                            fontWeight = FontWeight.SemiBold
                        )
                    }
                }
            }

            // Bottom Actions: Stop & Snooze
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                modifier = Modifier.fillMaxWidth()
            ) {
                // Large Stop Alarm Button
                Button(
                    onClick = onStop,
                    modifier = Modifier
                        .fillMaxWidth(0.85f)
                        .height(64.dp),
                    shape = RoundedCornerShape(20.dp),
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color(0xFFEF4444) // Urgent red/coral
                    )
                ) {
                    Icon(
                        imageVector = AuraIcons.AlarmOff,
                        contentDescription = null,
                        tint = Color.White,
                        modifier = Modifier.size(24.dp)
                    )
                    Spacer(modifier = Modifier.width(12.dp))
                    Text(
                        text = "TẮT BÁO THỨC",
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        letterSpacing = 0.5.sp
                    )
                }

                Spacer(modifier = Modifier.height(16.dp))

                // Snooze Button
                OutlinedButton(
                    onClick = onSnooze,
                    modifier = Modifier
                        .fillMaxWidth(0.85f)
                        .height(52.dp),
                    shape = RoundedCornerShape(18.dp),
                    colors = ButtonDefaults.outlinedButtonColors(
                        contentColor = AuraNeonCyan
                    ),
                    border = ButtonDefaults.outlinedButtonBorder(enabled = true).copy(
                        brush = Brush.horizontalGradient(listOf(AuraNeonCyan, AuraNeonPink))
                    )
                ) {
                    Icon(
                        imageVector = AuraIcons.Snooze,
                        contentDescription = null,
                        modifier = Modifier.size(18.dp)
                    )
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        text = "BÁO LẠI 5 PHÚT",
                        fontSize = 15.sp,
                        fontWeight = FontWeight.SemiBold
                    )
                }
            }
        }
    }
}

@Composable
private fun CyberMorningBriefingView(
    onOpenChat: () -> Unit
) {
    val dateFormat = SimpleDateFormat("EEEE, dd 'tháng' MM", Locale("vi", "VN"))
    val todayDate = remember { dateFormat.format(Date()).replaceFirstChar { it.uppercase() } }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.verticalGradient(
                    colors = listOf(
                        Color(0xFF064E3B).copy(alpha = 0.25f), // Emerald sunrise hue
                        Color(0xFF030712)
                    )
                )
            )
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.SpaceBetween
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.fillMaxWidth()
        ) {
            Spacer(modifier = Modifier.height(32.dp))

            // Greeting Emblem
            Box(
                modifier = Modifier
                    .size(68.dp)
                    .clip(CircleShape)
                    .background(Color(0xFF10B981).copy(alpha = 0.15f))
                    .border(2.dp, Color(0xFF10B981), CircleShape),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = AuraIcons.Spark,
                    contentDescription = null,
                    tint = Color(0xFF34D399),
                    modifier = Modifier.size(36.dp)
                )
            }

            Spacer(modifier = Modifier.height(20.dp))

            Text(
                text = "Chào buổi sáng anh!",
                fontSize = 26.sp,
                fontWeight = FontWeight.Bold,
                color = Color.White
            )

            Text(
                text = todayDate,
                fontSize = 14.sp,
                color = Color(0xFF9CA3AF),
                modifier = Modifier.padding(top = 4.dp)
            )

            Spacer(modifier = Modifier.height(28.dp))

            // Morning Briefing Cards
            Column(
                verticalArrangement = Arrangement.spacedBy(14.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                BriefingCard(
                    icon = AuraIcons.Clock,
                    title = "Trạng thái ngày mới",
                    subtitle = "Aura đã thức dậy cùng anh. Hệ thống sẵn sàng đồng hành cả ngày."
                )

                BriefingCard(
                    icon = AuraIcons.Brain,
                    title = "Mục tiêu trọng tâm",
                    subtitle = "Kiểm tra lịch trình, hoàn thành công việc và giữ năng lượng tích cực!"
                )
            }
        }

        // Action: Open Aura
        Button(
            onClick = onOpenChat,
            modifier = Modifier
                .fillMaxWidth()
                .height(60.dp),
            shape = RoundedCornerShape(20.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = Color(0xFF10B981)
            )
        ) {
            Icon(
                imageVector = AuraIcons.ChatBubble,
                contentDescription = null,
                tint = Color.White,
                modifier = Modifier.size(20.dp)
            )
            Spacer(modifier = Modifier.width(10.dp))
            Text(
                text = "BẮT ĐẦU NGÀY MỚI CÙNG AURA",
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold
            )
        }
    }
}

@Composable
private fun BriefingCard(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    subtitle: String
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(Color(0xFF111827).copy(alpha = 0.7f))
            .border(1.dp, Color(0xFF1F2937), RoundedCornerShape(16.dp))
            .padding(16.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(
            modifier = Modifier
                .size(40.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(AuraNeonCyan.copy(alpha = 0.12f)),
            contentAlignment = Alignment.Center
        ) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = AuraNeonCyan,
                modifier = Modifier.size(22.dp)
            )
        }

        Spacer(modifier = Modifier.width(14.dp))

        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                color = Color.White,
                fontSize = 15.sp,
                fontWeight = FontWeight.SemiBold
            )
            Text(
                text = subtitle,
                color = Color(0xFF9CA3AF),
                fontSize = 13.sp,
                lineHeight = 18.sp,
                modifier = Modifier.padding(top = 2.dp)
            )
        }
    }
}
