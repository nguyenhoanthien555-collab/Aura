package com.aura.companion.ui.chat

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import com.aura.companion.ui.theme.auraGlassBlur
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.clickable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.aura.companion.R
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * One message.
 *
 * Aura's messages sit left on the surface colour, the user's sit right in
 * the primary container. A failed message keeps its text and gains a retry
 * action rather than disappearing - losing what someone typed because the
 * signal dropped is the fastest way to make an app untrustworthy.
 */
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.text.buildAnnotatedString
import android.widget.Toast
import androidx.compose.ui.platform.LocalContext

@Composable
fun MessageBubble(
    message: ChatMessage,
    onRetry: () -> Unit,
    onReact: (String) -> Unit,
) {

    val fromUser = message.author == ChatMessage.Author.USER
    val clipboardManager = LocalClipboardManager.current
    val context = LocalContext.current
    var showMenu by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(false) }

    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = if (fromUser) Alignment.End else Alignment.Start,
    ) {

        Box {
            val bubbleShape = RoundedCornerShape(24.dp)
            val bubbleColor = when {
                message.failed -> MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.5f)
                fromUser -> MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.5f)
                else -> MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f)
            }
            Box(
                modifier = Modifier
                    .widthIn(max = 300.dp)
                    .auraGlassBlur(
                        shape = bubbleShape,
                        tint = bubbleColor
                    )
                    .pointerInput(Unit) {
                        detectTapGestures(
                            onLongPress = {
                                showMenu = true
                            }
                        )
                    },
            ) {
                val codeBg = if (fromUser) MaterialTheme.colorScheme.primary.copy(alpha = 0.2f) else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.1f)
                Text(
                    text = parseMarkdownToAnnotatedString(message.text, codeColor = codeBg),
                    style = MaterialTheme.typography.bodyLarge,
                    color = when {
                        message.failed -> MaterialTheme.colorScheme.onErrorContainer
                        fromUser -> MaterialTheme.colorScheme.onPrimaryContainer
                        else -> MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
                )
                
                androidx.compose.material3.DropdownMenu(
                    expanded = showMenu,
                    onDismissRequest = { showMenu = false }
                ) {
                    // Reaction Bar
                    Row(
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                        horizontalArrangement = Arrangement.spacedBy(16.dp)
                    ) {
                        listOf("❤️", "👍", "😂", "😲", "😢", "🙏").forEach { emoji ->
                            Text(
                                text = emoji,
                                style = MaterialTheme.typography.titleLarge,
                                modifier = Modifier.clickable {
                                    showMenu = false
                                    onReact(emoji)
                                }
                            )
                        }
                    }
                    androidx.compose.material3.DropdownMenuItem(
                        text = { Text("Copy text") },
                        onClick = {
                            showMenu = false
                            clipboardManager.setText(buildAnnotatedString { append(message.text) })
                            Toast.makeText(context, "Copied to clipboard", Toast.LENGTH_SHORT).show()
                        }
                    )
                }
            }
            
            if (message.reactions.isNotEmpty()) {
                Surface(
                    shape = RoundedCornerShape(12.dp),
                    color = MaterialTheme.colorScheme.surface,
                    modifier = Modifier
                        .align(if (fromUser) Alignment.BottomStart else Alignment.BottomEnd)
                        .padding(horizontal = 12.dp)
                        .offset(y = 12.dp)
                ) {
                    Row(modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)) {
                        message.reactions.values.toSet().forEach { emoji ->
                            Text(text = emoji, style = MaterialTheme.typography.labelMedium)
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(4.dp))
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(top = 8.dp, start = 8.dp, end = 8.dp),
        ) {

            Text(
                text = formatTime(message.timestamp),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
            )

            if (message.failed) {
                TextButton(
                    onClick = onRetry,
                    contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
                ) {
                    Text(
                        text = stringResource(R.string.action_retry),
                        style = MaterialTheme.typography.labelSmall,
                    )
                }
            }
        }
    }
}

/**
 * The connection line under the title.
 *
 * Every state says what to do next, or explicitly says to wait. "Waking
 * up" is the one that earns its place: on a free tier the first request
 * after an idle period takes tens of seconds, and without an explanation
 * a user assumes the app is broken.
 */
@Composable
fun ConnectionLabel(connection: ConnectionState) {

    val (text, colour) = when (connection) {

        ConnectionState.Unknown ->
            stringResource(R.string.connection_unknown) to
                MaterialTheme.colorScheme.onSurfaceVariant

        ConnectionState.Connecting ->
            stringResource(R.string.connection_connecting) to
                MaterialTheme.colorScheme.onSurfaceVariant

        ConnectionState.WakingUp ->
            stringResource(R.string.connection_waking) to
                MaterialTheme.colorScheme.tertiary

        is ConnectionState.Connected ->
            stringResource(R.string.connection_connected, connection.provider) to
                MaterialTheme.colorScheme.primary

        is ConnectionState.Unavailable ->
            connection.reason to MaterialTheme.colorScheme.error
    }

    Text(
        text = text,
        style = MaterialTheme.typography.labelSmall,
        color = colour,
        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
    )
}

/**
 * A dismissible error strip.
 *
 * Carries an action only when there is one worth offering - an
 * unconfigured app can be sent to Settings, a timeout cannot be fixed by
 * tapping anything.
 */
@Composable
fun ErrorBanner(
    message: String,
    onDismiss: () -> Unit,
    onAction: (() -> Unit)? = null,
) {
    Surface(
        color = MaterialTheme.colorScheme.errorContainer,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(start = 16.dp, end = 8.dp, top = 6.dp, bottom = 6.dp),
        ) {
            Text(
                text = message,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onErrorContainer,
                modifier = Modifier
                    .weight(1f)
                    .semantics { liveRegion = LiveRegionMode.Assertive },
            )

            if (onAction != null) {
                TextButton(onClick = onAction) {
                    Text(stringResource(R.string.action_open_settings))
                }
            }

            TextButton(onClick = onDismiss) {
                Text(stringResource(R.string.action_dismiss))
            }
        }
    }
}

/**
 * What the screen says before anyone has typed anything.
 */
@Composable
fun EmptyConversation(
    isConfigured: Boolean,
    onOpenSettings: () -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(32.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {

            Text(
                text = stringResource(
                    if (isConfigured) R.string.empty_title else R.string.empty_title_setup
                ),
                style = MaterialTheme.typography.titleMedium,
                textAlign = TextAlign.Center,
            )

            Spacer(Modifier.height(8.dp))

            Text(
                text = stringResource(
                    if (isConfigured) R.string.empty_body else R.string.empty_body_setup
                ),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )

            if (!isConfigured) {
                Spacer(Modifier.height(16.dp))
                TextButton(onClick = onOpenSettings) {
                    Text(stringResource(R.string.action_open_settings))
                }
            }
        }
    }
}

/**
 * Three dots, while Aura is thinking.
 */
@Composable
fun TypingIndicator(modifier: Modifier = Modifier) {

    val transition = rememberInfiniteTransition(label = "typing")

    Surface(
        shape = RoundedCornerShape(24.dp),
        color = MaterialTheme.colorScheme.surfaceVariant,
        modifier = modifier.semantics {
            liveRegion = LiveRegionMode.Polite
        },
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 12.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            repeat(3) { index ->

                val alpha by transition.animateFloat(
                    initialValue = 0.25f,
                    targetValue = 1f,
                    animationSpec = infiniteRepeatable(
                        animation = tween(600, delayMillis = index * 150),
                        repeatMode = RepeatMode.Reverse,
                    ),
                    label = "dot$index",
                )

                Box(
                    modifier = Modifier
                        .size(7.dp)
                        .alpha(alpha)
                        .background(
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            shape = CircleShape,
                        )
                )
            }
        }
    }
}

/**
 * The input row.
 *
 * Multi-line up to a point, because a companion app gets paragraphs and a
 * single-line field turns those into a scrolling slot. The send button is
 * disabled rather than hidden while a request is in flight, so its
 * position never moves under a thumb.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Composer(
    draft: String,
    canSend: Boolean,
    isSending: Boolean,
    onDraftChanged: (String) -> Unit,
    onSend: () -> Unit,
) {
    Surface(
        tonalElevation = 3.dp,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            verticalAlignment = Alignment.Bottom,
            modifier = Modifier
                .navigationBarsPadding()
                .padding(horizontal = 12.dp, vertical = 8.dp),
        ) {

            OutlinedTextField(
                value = draft,
                onValueChange = onDraftChanged,
                modifier = Modifier.weight(1f),
                placeholder = { Text(stringResource(R.string.composer_hint)) },
                maxLines = 5,
                shape = RoundedCornerShape(24.dp),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { if (canSend) onSend() }),
            )

            Spacer(Modifier.size(8.dp))

            FilledIconButton(
                onClick = onSend,
                enabled = canSend,
                modifier = Modifier
                    .padding(bottom = 4.dp)
                    .size(48.dp),
            ) {
                if (isSending) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        strokeWidth = 2.dp,
                        color = MaterialTheme.colorScheme.onPrimary,
                    )
                } else {
                    Icon(
                        imageVector = Icons.Filled.Send,
                        contentDescription = stringResource(R.string.action_send),
                    )
                }
            }
        }
    }
}

private val timeFormat = SimpleDateFormat("HH:mm", Locale.getDefault())

private fun formatTime(millis: Long): String = timeFormat.format(Date(millis))
