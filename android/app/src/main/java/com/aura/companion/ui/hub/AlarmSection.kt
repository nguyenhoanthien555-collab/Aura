package com.aura.companion.ui.hub

import android.widget.Toast
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
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
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import com.aura.companion.alarm.data.AlarmStore
import com.aura.companion.alarm.model.AuraAlarm
import com.aura.companion.alarm.service.AlarmScheduler
import com.aura.companion.ui.theme.AuraIcons
import com.aura.companion.ui.theme.AuraNeonCyan
import com.aura.companion.ui.theme.AuraNeonPink
import com.aura.companion.ui.theme.auraGlassBlur

/**
 * Control Hub Section for Managing Aura Alarms.
 * Provides real-time creation, toggling, deletion, and quick-test simulation of the intelligent alarm system.
 */
@Composable
fun AlarmSection(
    onBack: () -> Unit,
    bottomBar: @Composable () -> Unit = {},
) {
    val context = LocalContext.current
    val store = remember { AlarmStore(context) }
    val scheduler = remember { AlarmScheduler(context, store) }
    val alarms by store.alarms.collectAsState()

    var showAddDialog by remember { mutableStateOf(false) }

    HubSection(
        title = "Báo thức Aura",
        subtitle = "Quản lý giờ thức dậy & Morning Briefing",
        onBack = onBack,
        bottomBar = bottomBar,
    ) {
        // Top Hero Card: Quick Overview & 5s Simulation Test
        QuickTestHeroCard(
            alarmCount = alarms.count { it.isEnabled },
            onQuickTest = {
                val testAlarm = AuraAlarm(
                    hour = 0,
                    minute = 0,
                    label = "Thử nghiệm Báo thức Aura",
                    repeatDays = emptyList()
                )
                // Schedule trigger in 5 seconds
                scheduler.snooze(testAlarm.id, minutes = 0) // trigger soon
                val triggerMillis = System.currentTimeMillis() + 5000L
                val am = context.getSystemService(android.content.Context.ALARM_SERVICE) as? android.app.AlarmManager
                val triggerIntent = android.content.Intent(context, com.aura.companion.alarm.receiver.AuraAlarmReceiver::class.java).apply {
                    action = com.aura.companion.alarm.receiver.AuraAlarmReceiver.ACTION_TRIGGER
                    putExtra(com.aura.companion.alarm.receiver.AuraAlarmReceiver.EXTRA_ALARM_ID, "test_5s")
                    putExtra(com.aura.companion.alarm.receiver.AuraAlarmReceiver.EXTRA_ALARM_LABEL, "Thử nghiệm Báo thức Aura (5s)")
                }
                val pi = android.app.PendingIntent.getBroadcast(
                    context,
                    99999,
                    triggerIntent,
                    android.app.PendingIntent.FLAG_UPDATE_CURRENT or android.app.PendingIntent.FLAG_IMMUTABLE
                )
                am?.setExactAndAllowWhileIdle(android.app.AlarmManager.RTC_WAKEUP, triggerMillis, pi)
                Toast.makeText(context, "⏰ Sẽ reo sau 5 giây! Anh có thể tắt màn hình để thử nghiệm.", Toast.LENGTH_LONG).show()
            },
            onAddAlarm = { showAddDialog = true }
        )

        Spacer(modifier = Modifier.height(18.dp))

        // Alarms List Header
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically
        ) {
            Text(
                text = "DANH SÁCH BÁO THỨC (${alarms.size})",
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                fontFamily = FontFamily.Monospace,
                color = Color(0xFF9CA3AF),
                letterSpacing = 1.sp
            )
        }

        Spacer(modifier = Modifier.height(10.dp))

        if (alarms.isEmpty()) {
            EmptyAlarmsCard(onAddAlarm = { showAddDialog = true })
        } else {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                alarms.forEach { alarm ->
                    AlarmItemCard(
                        alarm = alarm,
                        onToggle = { enabled ->
                            val updated = store.toggle(alarm.id, enabled)
                            if (updated != null) {
                                if (enabled) scheduler.schedule(updated) else scheduler.cancel(alarm.id)
                            }
                        },
                        onDelete = {
                            scheduler.cancel(alarm.id)
                            store.delete(alarm.id)
                            Toast.makeText(context, "Đã xóa báo thức ${alarm.formattedTime}", Toast.LENGTH_SHORT).show()
                        }
                    )
                }
            }
        }
    }

    if (showAddDialog) {
        AddAlarmDialog(
            onDismiss = { showAddDialog = false },
            onConfirm = { hour, minute, label, repeatDays ->
                val newAlarm = AuraAlarm(
                    hour = hour,
                    minute = minute,
                    label = label.ifBlank { "Báo thức Aura" },
                    repeatDays = repeatDays
                )
                store.save(newAlarm)
                scheduler.schedule(newAlarm)
                showAddDialog = false
                Toast.makeText(context, "Đã đặt báo thức lúc ${newAlarm.formattedTime}!", Toast.LENGTH_SHORT).show()
            }
        )
    }
}

@Composable
private fun QuickTestHeroCard(
    alarmCount: Int,
    onQuickTest: () -> Unit,
    onAddAlarm: () -> Unit,
) {
    val shape = RoundedCornerShape(20.dp)
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .border(
                1.dp,
                Brush.horizontalGradient(listOf(AuraNeonCyan.copy(alpha = 0.5f), AuraNeonPink.copy(alpha = 0.3f))),
                shape
            )
            .background(Color(0xFF0F172A).copy(alpha = 0.85f))
            .padding(18.dp)
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.SpaceBetween
            ) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(
                        modifier = Modifier
                            .size(38.dp)
                            .clip(CircleShape)
                            .background(AuraNeonCyan.copy(alpha = 0.15f)),
                        contentAlignment = Alignment.Center
                    ) {
                        Icon(
                            imageVector = AuraIcons.Alarm,
                            contentDescription = null,
                            tint = AuraNeonCyan,
                            modifier = Modifier.size(20.dp)
                        )
                    }
                    Spacer(modifier = Modifier.width(10.dp))
                    Column {
                        Text(
                            text = "Hệ thống Báo thức Cyber",
                            fontWeight = FontWeight.Bold,
                            fontSize = 16.sp,
                            color = Color.White
                        )
                        Text(
                            text = if (alarmCount > 0) "$alarmCount báo thức đang kích hoạt" else "Chưa có báo thức nào bật",
                            fontSize = 12.sp,
                            color = if (alarmCount > 0) Color(0xFF34D399) else Color(0xFF9CA3AF)
                        )
                    }
                }
            }

            Text(
                text = "Báo thức hoạt động 100% cục bộ trên máy, tự động mở khóa màn hình và đọc Bản tin Buổi Sáng khi anh thức dậy.",
                fontSize = 13.sp,
                color = Color(0xFF94A3B8),
                lineHeight = 18.sp
            )

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Button(
                    onClick = onAddAlarm,
                    modifier = Modifier.weight(1f).height(46.dp),
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = AuraNeonCyan)
                ) {
                    Icon(
                        imageVector = AuraIcons.Add,
                        contentDescription = null,
                        tint = Color(0xFF030712),
                        modifier = Modifier.size(16.dp)
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(
                        text = "Thêm mới",
                        fontWeight = FontWeight.Bold,
                        color = Color(0xFF030712),
                        fontSize = 13.sp
                    )
                }

                OutlinedButton(
                    onClick = onQuickTest,
                    modifier = Modifier.weight(1f).height(46.dp),
                    shape = RoundedCornerShape(12.dp),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = Color.White),
                    border = ButtonDefaults.outlinedButtonBorder.copy(
                        brush = Brush.horizontalGradient(listOf(AuraNeonPink, AuraNeonCyan))
                    )
                ) {
                    Icon(
                        imageVector = AuraIcons.Clock,
                        contentDescription = null,
                        tint = AuraNeonPink,
                        modifier = Modifier.size(16.dp)
                    )
                    Spacer(modifier = Modifier.width(6.dp))
                    Text(
                        text = "Thử sau 5s",
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 13.sp
                    )
                }
            }
        }
    }
}

@Composable
private fun AlarmItemCard(
    alarm: AuraAlarm,
    onToggle: (Boolean) -> Unit,
    onDelete: () -> Unit,
) {
    val shape = RoundedCornerShape(18.dp)
    val dayNames = listOf("T2", "T3", "T4", "T5", "T6", "T7", "CN")

    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .border(
                1.dp,
                if (alarm.isEnabled) AuraNeonCyan.copy(alpha = 0.35f) else Color(0xFF334155),
                shape
            )
            .background(Color(0xFF111827).copy(alpha = 0.8f))
            .padding(16.dp)
    ) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Column(modifier = Modifier.weight(1f)) {
                // Time
                Text(
                    text = alarm.formattedTime,
                    fontSize = 32.sp,
                    fontWeight = FontWeight.ExtraBold,
                    fontFamily = FontFamily.Monospace,
                    color = if (alarm.isEnabled) Color.White else Color(0xFF6B7280),
                    letterSpacing = (-1).sp
                )

                Spacer(modifier = Modifier.height(4.dp))

                // Label
                Text(
                    text = alarm.label,
                    fontSize = 14.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = if (alarm.isEnabled) AuraNeonCyan else Color(0xFF9CA3AF)
                )

                Spacer(modifier = Modifier.height(6.dp))

                // Repeat Days Chips
                if (alarm.repeatDays.isEmpty()) {
                    Text(
                        text = "Một lần",
                        fontSize = 11.sp,
                        color = Color(0xFF94A3B8),
                        fontFamily = FontFamily.Monospace
                    )
                } else {
                    Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        (1..7).forEach { dayNum ->
                            val isSelected = alarm.repeatDays.contains(dayNum)
                            Text(
                                text = dayNames[dayNum - 1],
                                fontSize = 10.sp,
                                fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Normal,
                                color = if (isSelected && alarm.isEnabled) AuraNeonCyan else if (isSelected) Color(0xFF9CA3AF) else Color(0xFF475569),
                                modifier = Modifier
                                    .clip(RoundedCornerShape(4.dp))
                                    .background(if (isSelected && alarm.isEnabled) AuraNeonCyan.copy(alpha = 0.15f) else Color.Transparent)
                                    .padding(horizontal = 4.dp, vertical = 2.dp)
                            )
                        }
                    }
                }
            }

            // Actions: Switch & Delete
            Row(verticalAlignment = Alignment.CenterVertically) {
                Switch(
                    checked = alarm.isEnabled,
                    onCheckedChange = onToggle,
                    colors = SwitchDefaults.colors(
                        checkedThumbColor = Color.White,
                        checkedTrackColor = AuraNeonCyan,
                        uncheckedThumbColor = Color(0xFF9CA3AF),
                        uncheckedTrackColor = Color(0xFF374151)
                    )
                )

                Spacer(modifier = Modifier.width(8.dp))

                IconButton(onClick = onDelete) {
                    Icon(
                        imageVector = AuraIcons.Purge,
                        contentDescription = "Xóa",
                        tint = Color(0xFFEF4444).copy(alpha = 0.8f),
                        modifier = Modifier.size(20.dp)
                    )
                }
            }
        }
    }
}

@Composable
private fun EmptyAlarmsCard(onAddAlarm: () -> Unit) {
    val shape = RoundedCornerShape(16.dp)
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .clip(shape)
            .border(1.dp, Color(0xFF1E293B), shape)
            .background(Color(0xFF0F172A).copy(alpha = 0.5f))
            .clickable(onClick = onAddAlarm)
            .padding(28.dp),
        contentAlignment = Alignment.Center
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            Icon(
                imageVector = AuraIcons.AlarmOff,
                contentDescription = null,
                tint = Color(0xFF64748B),
                modifier = Modifier.size(40.dp)
            )
            Spacer(modifier = Modifier.height(12.dp))
            Text(
                text = "Chưa có báo thức nào",
                fontSize = 15.sp,
                fontWeight = FontWeight.SemiBold,
                color = Color.White
            )
            Spacer(modifier = Modifier.height(4.dp))
            Text(
                text = "Chạm vào đây để tạo mới, hoặc nói với Aura:\n\"Gọi anh dậy lúc 7h sáng mai nhé\"",
                fontSize = 12.sp,
                color = Color(0xFF94A3B8),
                textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                lineHeight = 16.sp
            )
        }
    }
}

@Composable
private fun AddAlarmDialog(
    onDismiss: () -> Unit,
    onConfirm: (hour: Int, minute: Int, label: String, repeatDays: List<Int>) -> Unit
) {
    var hourText by remember { mutableStateOf("07") }
    var minuteText by remember { mutableStateOf("00") }
    var labelText by remember { mutableStateOf("Báo thức sáng") }
    val selectedDays = remember { mutableStateOf(setOf(1, 2, 3, 4, 5)) } // Mon-Fri default

    val dayLabels = listOf("T2", "T3", "T4", "T5", "T6", "T7", "CN")

    Dialog(onDismissRequest = onDismiss) {
        Surface(
            shape = RoundedCornerShape(24.dp),
            color = Color(0xFF111827),
            border = androidx.compose.foundation.BorderStroke(1.dp, AuraNeonCyan.copy(alpha = 0.4f)),
            modifier = Modifier.fillMaxWidth().padding(8.dp)
        ) {
            Column(
                modifier = Modifier.padding(20.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Text(
                    text = "HẸN BÁO THỨC MỚI",
                    fontSize = 15.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = Color.White,
                    letterSpacing = 1.sp
                )

                Spacer(modifier = Modifier.height(20.dp))

                // Digital Time Input (HH : MM)
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.Center
                ) {
                    OutlinedTextField(
                        value = hourText,
                        onValueChange = { if (it.length <= 2) hourText = it },
                        modifier = Modifier.width(80.dp),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        singleLine = true,
                        textStyle = androidx.compose.ui.text.TextStyle(
                            fontSize = 28.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
                            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                            color = AuraNeonCyan
                        ),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = AuraNeonCyan,
                            unfocusedBorderColor = Color(0xFF374151)
                        )
                    )

                    Text(
                        text = ":",
                        fontSize = 32.sp,
                        fontWeight = FontWeight.Bold,
                        color = Color.White,
                        modifier = Modifier.padding(horizontal = 10.dp)
                    )

                    OutlinedTextField(
                        value = minuteText,
                        onValueChange = { if (it.length <= 2) minuteText = it },
                        modifier = Modifier.width(80.dp),
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                        singleLine = true,
                        textStyle = androidx.compose.ui.text.TextStyle(
                            fontSize = 28.sp,
                            fontWeight = FontWeight.Bold,
                            fontFamily = FontFamily.Monospace,
                            textAlign = androidx.compose.ui.text.style.TextAlign.Center,
                            color = AuraNeonCyan
                        ),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = AuraNeonCyan,
                            unfocusedBorderColor = Color(0xFF374151)
                        )
                    )
                }

                Spacer(modifier = Modifier.height(16.dp))

                // Label Field
                OutlinedTextField(
                    value = labelText,
                    onValueChange = { labelText = it },
                    label = { Text("Nhãn báo thức") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    colors = OutlinedTextFieldDefaults.colors(
                        focusedBorderColor = AuraNeonCyan,
                        unfocusedBorderColor = Color(0xFF374151),
                        focusedLabelColor = AuraNeonCyan,
                        unfocusedLabelColor = Color(0xFF9CA3AF)
                    )
                )

                Spacer(modifier = Modifier.height(16.dp))

                // Repeat Days Selection
                Text(
                    text = "LẶP LẠI HÀNG TUẦN",
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    fontFamily = FontFamily.Monospace,
                    color = Color(0xFF9CA3AF),
                    modifier = Modifier.fillMaxWidth()
                )

                Spacer(modifier = Modifier.height(8.dp))

                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween
                ) {
                    (1..7).forEach { dayNum ->
                        val isSelected = selectedDays.value.contains(dayNum)
                        Box(
                            modifier = Modifier
                                .size(36.dp)
                                .clip(CircleShape)
                                .background(if (isSelected) AuraNeonCyan else Color(0xFF1F2937))
                                .clickable {
                                    val current = selectedDays.value.toMutableSet()
                                    if (current.contains(dayNum)) current.remove(dayNum) else current.add(dayNum)
                                    selectedDays.value = current
                                },
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                text = dayLabels[dayNum - 1],
                                fontSize = 11.sp,
                                fontWeight = FontWeight.Bold,
                                color = if (isSelected) Color(0xFF030712) else Color(0xFF9CA3AF)
                            )
                        }
                    }
                }

                Spacer(modifier = Modifier.height(24.dp))

                // Actions: Cancel & Save
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End
                ) {
                    TextButton(onClick = onDismiss) {
                        Text("HỦY", color = Color(0xFF9CA3AF), fontWeight = FontWeight.SemiBold)
                    }

                    Spacer(modifier = Modifier.width(8.dp))

                    Button(
                        onClick = {
                            val h = hourText.toIntOrNull()?.coerceIn(0, 23) ?: 7
                            val m = minuteText.toIntOrNull()?.coerceIn(0, 59) ?: 0
                            onConfirm(h, m, labelText, selectedDays.value.toList().sorted())
                        },
                        colors = ButtonDefaults.buttonColors(containerColor = AuraNeonCyan)
                    ) {
                        Text("LƯU BÁO THỨC", color = Color(0xFF030712), fontWeight = FontWeight.Bold)
                    }
                }
            }
        }
    }
}
