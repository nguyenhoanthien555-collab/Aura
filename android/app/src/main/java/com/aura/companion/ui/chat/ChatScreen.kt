package com.aura.companion.ui.chat

import androidx.compose.animation.AnimatedVisibility
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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.ChatBubble
import androidx.compose.material.icons.filled.Stop
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Surface
import androidx.compose.ui.text.style.TextOverflow
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
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
                    IconButton(onClick = {
                        context.startService(android.content.Intent(context, com.aura.companion.floating.FloatingChatService::class.java))
                    }) {
                        Icon(
                            imageVector = Icons.Filled.ChatBubble,
                            contentDescription = "Floating Bubble",
                        )
                    }
                    if (state.messages.isNotEmpty()) {
                        IconButton(onClick = viewModel::newConversation) {
                            Icon(
                                imageVector = Icons.Filled.Refresh,
                                contentDescription = stringResource(
                                    R.string.action_new_conversation
                                ),
                            )
                        }
                    }
                    IconButton(onClick = onOpenSettings) {
                        Icon(
                            imageVector = Icons.Filled.Settings,
                            contentDescription = stringResource(R.string.action_settings),
                        )
                    }
                },
            )
        },
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

            // Aura's face + status at the top of the chat: her present
            // expression and what she's doing, in words. Only here now - the
            // per-message faces were removed at the owner's request.
            AuraPresence(state = state)

            Box(modifier = Modifier.weight(1f)) {

                if (state.messages.isEmpty()) {
                    EmptyConversation(
                        isConfigured = state.isConfigured,
                        onOpenSettings = onOpenSettings,
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
                                    onReact = { emoji -> viewModel.react(message.id, emoji) }
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

            AnimatedVisibility(visible = state.isAgentRunning || state.isSending) {
                androidx.compose.material3.Surface(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 4.dp),
                    color = MaterialTheme.colorScheme.secondaryContainer.copy(alpha = 0.9f),
                    shape = RoundedCornerShape(12.dp),
                    tonalElevation = 3.dp,
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Row(
                            modifier = Modifier.weight(1f),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            CircularProgressIndicator(
                                modifier = Modifier
                                    .padding(end = 8.dp)
                                    .size(16.dp),
                                strokeWidth = 2.dp,
                                color = MaterialTheme.colorScheme.secondary,
                            )
                            Text(
                                text = state.agentStatusText.ifBlank { "Aura đang xử lý..." },
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.onSecondaryContainer,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                        Button(
                            onClick = viewModel::interruptAgent,
                            colors = ButtonDefaults.buttonColors(
                                containerColor = MaterialTheme.colorScheme.error,
                                contentColor = MaterialTheme.colorScheme.onError,
                            ),
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 2.dp),
                            shape = RoundedCornerShape(8.dp),
                        ) {
                            Icon(
                                imageVector = Icons.Filled.Stop,
                                contentDescription = "Dừng lại",
                                modifier = Modifier.size(16.dp),
                            )
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("Dừng lại", style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            }

            Composer(
                draft = state.draft,
                canSend = state.canSend,
                isSending = state.isSending,
                onDraftChanged = viewModel::onDraftChanged,
                onSend = viewModel::send,
            )
        }
    }
}
