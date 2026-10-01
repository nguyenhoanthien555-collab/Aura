package com.aura.companion.ui.chat

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aura.companion.R
import com.aura.companion.ui.theme.AuraIcons
import com.aura.companion.ui.theme.auraGlassBlur
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
        val bubbleShape = if (fromUser) {
            RoundedCornerShape(topStart = 20.dp, topEnd = 20.dp, bottomStart = 20.dp, bottomEnd = 4.dp)
        } else {
            RoundedCornerShape(topStart = 4.dp, topEnd = 20.dp, bottomStart = 20.dp, bottomEnd = 20.dp)
        }

        val borderStroke = if (fromUser) {
            androidx.compose.foundation.BorderStroke(1.dp, Color(0xFFA78BFA).copy(alpha = 0.35f))
        } else {
            androidx.compose.foundation.BorderStroke(1.dp, Color(0xFF06B6D4).copy(alpha = 0.3f))
        }

        val bubbleBg = if (fromUser) {
            Color(0xFF5B21B6).copy(alpha = 0.65f)
        } else {
            Color(0xFF131224).copy(alpha = 0.7f)
        }

        Box(
            modifier = Modifier
                .widthIn(max = 320.dp)
                .clip(bubbleShape)
                .border(borderStroke, bubbleShape)
                .auraGlassBlur(
                    shape = bubbleShape,
                    tint = bubbleBg,
                )
                .pointerInput(Unit) {
                    detectTapGestures(
                        onLongPress = { showMenu = true }
                    )
                }
                .padding(horizontal = 14.dp, vertical = 10.dp)
        ) {
            Column {
                if (!fromUser) {
                    // Futuristic Aura Tag Header
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(bottom = 4.dp),
                    ) {
                        Icon(
                            imageVector = AuraIcons.Spark,
                            contentDescription = null,
                            tint = Color(0xFF38BDF8),
                            modifier = Modifier.size(11.dp),
                        )
                        Spacer(Modifier.width(4.dp))
                        Text(
                            text = "AURA",
                            style = MaterialTheme.typography.labelSmall.copy(
                                fontSize = 9.sp,
                                fontFamily = FontFamily.Monospace,
                                fontWeight = FontWeight.Bold,
                                letterSpacing = 1.sp,
                            ),
                            color = Color(0xFF38BDF8),
                        )
                    }
                }

                val codeBg = if (fromUser) Color(0xFF3B0764).copy(alpha = 0.5f) else Color(0xFF1E293B).copy(alpha = 0.6f)
                val linkColor = if (fromUser) Color(0xFFC084FC) else Color(0xFF38BDF8)
                val rendered = androidx.compose.runtime.remember(message.text, codeBg, linkColor) {
                    parseMarkdownToAnnotatedString(message.text, codeColor = codeBg, linkColor = linkColor)
                }

                Text(
                    text = rendered,
                    style = MaterialTheme.typography.bodyMedium.copy(lineHeight = 21.sp),
                    color = if (fromUser) Color(0xFFF3E8FF) else Color(0xFFF1F5F9),
                )

                // Timestamp & Status Footer inside Bubble
                Row(
                    modifier = Modifier
                        .align(Alignment.End)
                        .padding(top = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    if (message.verified == true) {
                        Icon(
                            imageVector = AuraIcons.Verified,
                            contentDescription = "Verified",
                            tint = Color(0xFF34D399),
                            modifier = Modifier.size(11.dp),
                        )
                        Text(
                            text = "Verified",
                            style = MaterialTheme.typography.labelSmall.copy(fontSize = 9.sp),
                            color = Color(0xFF34D399),
                        )
                    }
                    Text(
                        text = formatTime(message.timestamp),
                        style = MaterialTheme.typography.labelSmall.copy(
                            fontSize = 9.sp,
                            fontFamily = FontFamily.Monospace,
                        ),
                        color = Color(0xFF94A3B8),
                    )
                }
            }

            // Dropdown Menu for Copy & Reaction
            androidx.compose.material3.DropdownMenu(
                expanded = showMenu,
                onDismissRequest = { showMenu = false }
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(16.dp),
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
                    text = { Text("Sao chép") },
                    onClick = {
                        showMenu = false
                        clipboardManager.setText(buildAnnotatedString { append(message.text) })
                        Toast.makeText(context, "Đã sao chép", Toast.LENGTH_SHORT).show()
                    }
                )
            }
        }

        // Reaction chips below bubble
        if (message.reactions.isNotEmpty()) {
            Surface(
                shape = RoundedCornerShape(12.dp),
                color = Color(0xFF1E1B4B).copy(alpha = 0.8f),
                border = androidx.compose.foundation.BorderStroke(0.5.dp, Color(0xFF8B5CF6).copy(alpha = 0.4f)),
                modifier = Modifier.padding(top = 2.dp, start = 6.dp, end = 6.dp),
            ) {
                Row(
                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
                    horizontalArrangement = Arrangement.spacedBy(4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    message.reactions.values.distinct().forEach { emoji ->
                        Text(
                            text = "$emoji ${message.reactions.values.count { it == emoji }}",
                            style = MaterialTheme.typography.labelSmall.copy(fontSize = 11.sp),
                        )
                    }
                }
            }
        }

        if (message.failed) {
            TextButton(
                onClick = onRetry,
                contentPadding = PaddingValues(horizontal = 8.dp, vertical = 0.dp),
            ) {
                Icon(
                    imageVector = AuraIcons.Refresh,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.error,
                    modifier = Modifier.size(12.dp),
                )
                Spacer(Modifier.width(4.dp))
                Text(
                    text = stringResource(R.string.action_retry),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.error,
                )
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

        is ConnectionState.OnDevice ->
            "⚡ On-Device Brain (${connection.modelName})" to
                MaterialTheme.colorScheme.primary

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
    onSelectPrompt: (String) -> Unit = {},
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Surface(
                shape = CircleShape,
                color = Color(0xFF1E1B4B).copy(alpha = 0.6f),
                border = androidx.compose.foundation.BorderStroke(1.dp, Color(0xFF8B5CF6).copy(alpha = 0.5f)),
                modifier = Modifier.size(64.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(
                        imageVector = AuraIcons.Spark,
                        contentDescription = null,
                        tint = Color(0xFFC084FC),
                        modifier = Modifier.size(32.dp),
                    )
                }
            }

            Spacer(Modifier.height(16.dp))

            Text(
                text = stringResource(
                    if (isConfigured) R.string.empty_title else R.string.empty_title_setup
                ),
                style = MaterialTheme.typography.titleMedium.copy(fontWeight = FontWeight.Bold),
                textAlign = TextAlign.Center,
                color = MaterialTheme.colorScheme.onSurface,
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
                Spacer(Modifier.height(20.dp))
                Button(
                    onClick = onOpenSettings,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = Color(0xFF7C3AED),
                        contentColor = Color.White,
                    ),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text(stringResource(R.string.action_open_settings))
                }
            } else {
                Spacer(Modifier.height(24.dp))
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    val quickPrompts = listOf(
                        "⚡ Kiểm tra thông số phần cứng Laptop & Điện thoại" to "Hãy cho tôi biết chi tiết tình trạng phần cứng máy tính và điện thoại hiện tại.",
                        "🧠 Xem danh sách ký ức & tri thức đã ghi nhớ" to "Aura đang lưu giữ những sự thật và thông tin gì về tôi?",
                        "🔍 Tìm kiếm tin tức công nghệ hôm nay" to "Tìm kiếm tin tức công nghệ và AI nổi bật trong ngày hôm nay.",
                        "💬 Chào Aura, hôm nay mình làm việc nhé!" to "Chào Aura, hôm nay có việc gì đáng chú ý không em?",
                    )
                    quickPrompts.forEach { (label, prompt) ->
                        Surface(
                            shape = RoundedCornerShape(14.dp),
                            color = Color(0xFF131224).copy(alpha = 0.7f),
                            border = androidx.compose.foundation.BorderStroke(
                                0.75.dp,
                                Color(0xFF8B5CF6).copy(alpha = 0.3f),
                            ),
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { onSelectPrompt(prompt) }
                        ) {
                            Row(
                                modifier = Modifier.padding(horizontal = 14.dp, vertical = 11.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(
                                    text = label,
                                    style = MaterialTheme.typography.bodySmall.copy(fontWeight = FontWeight.Medium),
                                    color = Color(0xFFE2E8F0),
                                    modifier = Modifier.weight(1f),
                                )
                                Icon(
                                    imageVector = AuraIcons.ChevronRight,
                                    contentDescription = null,
                                    tint = Color(0xFF8B5CF6),
                                    modifier = Modifier.size(14.dp),
                                )
                            }
                        }
                    }
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
 * Floating glass capsule with smooth border glow and haptic styling.
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
    val composerShape = RoundedCornerShape(26.dp)
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .navigationBarsPadding()
            .padding(horizontal = 14.dp, vertical = 6.dp)
            .clip(composerShape)
            .border(
                1.dp,
                Brush.horizontalGradient(
                    listOf(
                        Color(0xFF8B5CF6).copy(alpha = 0.4f),
                        Color(0xFF06B6D4).copy(alpha = 0.4f),
                    )
                ),
                composerShape,
            )
            .auraGlassBlur(
                shape = composerShape,
                tint = Color(0xFF131224).copy(alpha = 0.85f),
            )
            .padding(horizontal = 10.dp, vertical = 6.dp),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.fillMaxWidth(),
        ) {
            BasicTextField(
                value = draft,
                onValueChange = onDraftChanged,
                modifier = Modifier
                    .weight(1f)
                    .padding(horizontal = 12.dp, vertical = 8.dp),
                textStyle = MaterialTheme.typography.bodyLarge.copy(
                    color = Color.White,
                ),
                cursorBrush = SolidColor(Color(0xFF8B5CF6)),
                maxLines = 5,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { if (canSend) onSend() }),
                decorationBox = { innerTextField ->
                    if (draft.isEmpty()) {
                        Text(
                            text = stringResource(R.string.composer_hint),
                            style = MaterialTheme.typography.bodyLarge,
                            color = Color(0xFF64748B),
                        )
                    }
                    innerTextField()
                }
            )

            FilledIconButton(
                onClick = onSend,
                enabled = canSend,
                colors = IconButtonDefaults.filledIconButtonColors(
                    containerColor = Color(0xFF7C3AED),
                    contentColor = Color.White,
                    disabledContainerColor = Color(0xFF1E1B4B).copy(alpha = 0.5f),
                    disabledContentColor = Color(0xFF475569),
                ),
                modifier = Modifier.size(42.dp),
            ) {
                if (isSending) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        strokeWidth = 2.dp,
                        color = Color.White,
                    )
                } else {
                    Icon(
                        imageVector = AuraIcons.Send,
                        contentDescription = stringResource(R.string.action_send),
                        modifier = Modifier.size(18.dp),
                    )
                }
            }
        }
    }
}

private val timeFormat = SimpleDateFormat("HH:mm", Locale.getDefault())

private fun formatTime(millis: Long): String = timeFormat.format(Date(millis))
