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

    // Follow the conversation as it grows, so a reply that arrives while
    // the user is reading does not land below the fold unseen.
    LaunchedEffect(state.messages.size) {
        if (state.messages.isNotEmpty()) {
            listState.animateScrollToItem(state.messages.lastIndex)
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
                            MessageBubble(
                                message = message,
                                onRetry = { viewModel.retry(message.id) },
                            )
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
