package com.aura.companion.ui.chat

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import com.aura.companion.ui.theme.AuraIcons
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.Color
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Surface
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import android.util.Log
import android.widget.Toast
import com.aura.companion.R

/**
 * The conversation.
 *
 * A pure function of [ChatUiState]. Every branch the user can end up in -
 * empty, sending, offline, unconfigured, waking up, a message that failed -
 * is a value in that state rather than a flag hidden in a Composable, which
 * is what makes each of them reachable in a test.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    viewModel: ChatViewModel,
    onOpenSettings: () -> Unit,
    onOpenAlarms: () -> Unit = {},
    bottomBar: @Composable () -> Unit = {},
) {

    val state by viewModel.state.collectAsStateWithLifecycle()

    val listState = rememberLazyListState()

    // Follow the conversation as it grows - including a reply streaming in
    // token-by-token (keyed on the last message's length, not just the count) -
    // but never yank the user back down while they've scrolled up to read
    // history. Instant scrollToItem, not animate, so a fast stream doesn't
    // queue a stutter of competing scroll animations.
    val lastLen = state.messages.lastOrNull()?.text?.length ?: 0
    LaunchedEffect(state.messages.size, lastLen) {
        if (state.messages.isEmpty()) return@LaunchedEffect
        val lastVisible = listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: -1
        if (lastVisible >= state.messages.lastIndex - 1) {
            listState.scrollToItem(state.messages.lastIndex)
        }
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(
                            text = stringResource(R.string.app_name),
                            style = MaterialTheme.typography.titleMedium,
                        )
                        ConnectionLabel(state.connection)
                    }
                },
                actions = {
                    val context = androidx.compose.ui.platform.LocalContext.current
                    var menuExpanded by remember { mutableStateOf(false) }

                    // 1. Hands-Free (Walkie-talkie) Toggle
                    IconButton(onClick = viewModel::toggleHandsFreeMode) {
                        Icon(
                            imageVector = AuraIcons.Headset,
                            contentDescription = if (state.isHandsFreeMode) "Tắt chế độ rảnh tay" else "Bật chế độ rảnh tay",
                            tint = if (state.isHandsFreeMode) Color(0xFF00E5FF) else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    // 2. TTS Voice Reading Toggle
                    IconButton(onClick = viewModel::toggleTts) {
                        Icon(
                            imageVector = if (state.isTtsEnabled) AuraIcons.VolumeUp else AuraIcons.VolumeOff,
                            contentDescription = if (state.isTtsEnabled) "Tắt đọc giọng nói" else "Bật đọc giọng nói",
                            tint = if (state.isTtsEnabled) Color(0xFF38BDF8) else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }

                    // 3. Overflow Menu (New chat, Floating bubble, Alarms, Settings)
                    Box {
                        IconButton(onClick = { menuExpanded = true }) {
                            Icon(
                                imageVector = AuraIcons.MoreVert,
                                contentDescription = "Tùy chọn khác",
                                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }

                        DropdownMenu(
                            expanded = menuExpanded,
                            onDismissRequest = { menuExpanded = false },
                        ) {
                            if (state.messages.isNotEmpty()) {
                                DropdownMenuItem(
                                    text = { Text("Đoạn chat mới") },
                                    leadingIcon = {
                                        Icon(
                                            imageVector = AuraIcons.Refresh,
                                            contentDescription = null,
                                            tint = MaterialTheme.colorScheme.primary,
                                        )
                                    },
                                    onClick = {
                                        menuExpanded = false
                                        viewModel.newConversation()
                                    },
                                )
                            }

                            DropdownMenuItem(
                                text = { Text("Bong bóng chat nổi") },
                                leadingIcon = {
                                    Icon(
                                        imageVector = AuraIcons.ChatBubble,
                                        contentDescription = null,
                                        tint = Color(0xFF00E5FF),
                                    )
                                },
                                onClick = {
                                    menuExpanded = false
                                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M && !Settings.canDrawOverlays(context)) {
                                        val intent = Intent(
                                            Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                                            Uri.parse("package:${context.packageName}")
                                        ).apply { addFlags(Intent.FLAG_ACTIVITY_NEW_TASK) }
                                        context.startActivity(intent)
                                        Toast.makeText(context, "Vui lòng cấp quyền 'Hiển thị trên ứng dụng khác' cho Aura", Toast.LENGTH_LONG).show()
                                    } else {
                                        try {
                                            val intent = Intent(context, com.aura.companion.floating.FloatingChatService::class.java)
                                            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                                                context.startForegroundService(intent)
                                            } else {
                                                context.startService(intent)
                                            }
                                        } catch (e: Exception) {
                                            Log.e("ChatScreen", "Failed to start FloatingChatService", e)
                                        }
                                    }
                                },
                            )

                            DropdownMenuItem(
                                text = { Text("Báo thức Aura") },
                                leadingIcon = {
                                    Icon(
                                        imageVector = AuraIcons.Alarm,
                                        contentDescription = null,
                                        tint = Color(0xFFF59E0B),
                                    )
                                },
                                onClick = {
                                    menuExpanded = false
                                    onOpenAlarms()
                                },
                            )

                            DropdownMenuItem(
                                text = { Text("Trung tâm điều khiển") },
                                leadingIcon = {
                                    Icon(
                                        imageVector = AuraIcons.Settings,
                                        contentDescription = null,
                                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                },
                                onClick = {
                                    menuExpanded = false
                                    onOpenSettings()
                                },
                            )
                        }
                    }
                },
            )
        },
        bottomBar = bottomBar,
    ) { padding ->

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .imePadding(),
        ) {

            AnimatedVisibility(visible = state.error != null) {
                state.error?.let { error ->
                    ErrorBanner(
                        message = error.userMessage,
                        onDismiss = viewModel::dismissError,
                        onAction = if (!state.isConfigured) onOpenSettings else null,
                    )
                }
            }

            AnimatedVisibility(visible = state.isInitialScanning) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 6.dp),
                ) {
                    LinearProgressIndicator(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(bottom = 4.dp),
                        color = MaterialTheme.colorScheme.primary,
                        trackColor = MaterialTheme.colorScheme.surfaceVariant,
                    )
                    Text(
                        text = state.scanStatusText.ifBlank { "Aura đang kết nối & nhận diện hệ thống..." },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            // Aura Cyber-Core Capsule: dual-device live telemetry, pulsing core, and detailed HUD sheet
            AuraCyberCoreCapsule(
                state = state,
                onRefreshTelemetry = viewModel::refreshTelemetry,
            )

            AnimatedVisibility(visible = state.isHandsFreeMode) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp)
                        .background(
                            color = Color(0x2200E5FF),
                            shape = RoundedCornerShape(8.dp)
                        )
                        .padding(horizontal = 12.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Icon(
                        imageVector = AuraIcons.Headset,
                        contentDescription = null,
                        tint = Color(0xFF00E5FF),
                        modifier = Modifier.size(16.dp),
                    )
                    Text(
                        text = if (state.isListening) "Đang lắng nghe bạn nói..." else if (state.isSpeaking) "Aura đang trả lời..." else "Chế độ rảnh tay (Hands-Free) sẵn sàng",
                        style = MaterialTheme.typography.labelSmall,
                        color = Color(0xFF00E5FF),
                        modifier = Modifier.weight(1f),
                    )
                    Text(
                        text = "Nói 'tạm biệt' để tắt",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            Box(modifier = Modifier.weight(1f)) {

                if (state.messages.isEmpty()) {
                    EmptyConversation(
                        isConfigured = state.isConfigured,
                        onOpenSettings = onOpenSettings,
                        onSelectPrompt = { prompt ->
                            viewModel.onDraftChanged(prompt)
                            viewModel.send()
                        },
                    )
                } else {
                    LazyColumn(
                        state = listState,
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        items(items = state.messages, key = { it.id }) { message ->
                            // animateItem() gives new bubbles a smooth fade+slide-in
                            // and animates reflow, instead of popping in.
                            Box(modifier = Modifier.fillMaxWidth().animateItem()) {
                                MessageBubble(
                                    message = message,
                                    onRetry = { viewModel.retry(message.id) },
                                    onReact = { emoji -> viewModel.react(message.id, emoji) },
                                    onSpeak = { text -> viewModel.speak(text) },
                                )
                            }
                        }
                    }
                }

                if (state.isSending) {
                    TypingIndicator(
                        modifier = Modifier
                            .align(Alignment.BottomStart)
                            .padding(start = 20.dp, bottom = 8.dp),
                    )
                }
            }

            AnimatedVisibility(visible = state.pendingToolConsent != null) {
                state.pendingToolConsent?.let { consent ->
                    ToolConsentCard(
                        consent = consent,
                        onApprove = { viewModel.approveToolConsent(consent.requestId) },
                        onDeny = { viewModel.denyToolConsent(consent.requestId) },
                    )
                }
            }

            Composer(
                draft = state.draft,
                canSend = state.canSend,
                isSending = state.isSending,
                onStop = viewModel::cancelCurrentTurn,
                isThinkingEnabled = state.isThinkingEnabled,
                onToggleThinking = viewModel::toggleThinkingMode,
                isListening = state.isListening,
                speechRmsDb = state.speechRmsDb,
                attachedImageBitmap = state.attachedImageBitmap,
                onDraftChanged = viewModel::onDraftChanged,
                onSend = viewModel::send,
                onStartVoice = viewModel::startVoiceInput,
                onStopVoice = viewModel::stopVoiceInput,
                onAttachImage = viewModel::attachImage,
                onClearAttachment = viewModel::clearAttachment,
            )
        }
    }
}
