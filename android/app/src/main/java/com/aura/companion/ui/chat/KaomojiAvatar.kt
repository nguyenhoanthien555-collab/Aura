package com.aura.companion.ui.chat

import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.theme.rememberReducedMotion
import kotlinx.coroutines.delay
import kotlin.random.Random

/**
 * Aura's face, made of punctuation.
 *
 * The owner asked for a kaomoji companion rather than a 3D model or a photo:
 * a face built from characters that changes expression as she talks. It is
 * deliberately cheap - a [Text] of a few glyphs, one coroutine, no bitmaps -
 * because it runs on a mid-range phone and often inside a floating overlay
 * that must not cost a frame budget of its own.
 *
 * WHAT DRIVES THE EXPRESSION
 * --------------------------
 * The model already emits a reaction per reply (`[REACT:…]` / the socket's
 * Reaction event), stored on the message as `reactions["aura"]`. That emoji is
 * mapped to an [AuraEmotion] here, so the face and the reaction can never
 * disagree - they are the same signal rendered twice.
 *
 * NO GLITCH
 * ---------
 * Every frame of a given face is the *same character count*, so on a monospace
 * font the text box never changes width and a blink can't make the layout jump
 * - which was the "glitching" the owner saw. Blinks are brief and infrequent;
 * the mouth moves only while a reply is actually arriving. Reduced motion holds
 * the open face and stops all of it.
 */
enum class AuraEmotion {
    NEUTRAL, HAPPY, LOVE, LAUGH, SURPRISED, SAD, THINKING, WINK, THANKS,
    EXCITED, SHY, COOL, ANGRY, CONFUSED, SLEEPY, DIZZY, PROUD,
}

/** Eyes-open, mid-blink, and mouth-open. */
private data class Face(val open: String, val blink: String, val talk: String)

private fun faceOf(emotion: AuraEmotion): Face = when (emotion) {
    AuraEmotion.NEUTRAL   -> Face("( ・_・ )", "( ・‿・ )", "( ・o・ )")
    AuraEmotion.HAPPY     -> Face("( ◕‿◕ )", "( ˘‿˘ )", "( ◕o◕ )")
    AuraEmotion.LOVE      -> Face("( ♡‿♡ )", "( ˘‿˘ )", "( ♡o♡ )")
    AuraEmotion.LAUGH     -> Face("( ≧▽≦ )", "( ≧‿≦ )", "( ≧o≦ )")
    AuraEmotion.SURPRISED -> Face("( o□o )", "( -□- )", "( oOo )")
    AuraEmotion.SAD       -> Face("( ╥_╥ )", "( ˘_˘ )", "( ╥o╥ )")
    AuraEmotion.THINKING  -> Face("( ・_・ )?", "( ・‿・ )?", "( ・～・ )?")
    AuraEmotion.WINK      -> Face("( ^_~ )", "( ^_^ )", "( ^o~ )")
    AuraEmotion.THANKS    -> Face("( ˘▽˘ )", "( ˘‿˘ )", "( ˘o˘ )")
    AuraEmotion.EXCITED   -> Face("( ★▽★ )", "( ˘▽˘ )", "( ★o★ )")
    AuraEmotion.SHY       -> Face("( >‿< )", "( -‿- )", "( >o< )")
    AuraEmotion.COOL      -> Face("( ￣▽￣ )", "( ￣‿￣ )", "( ￣o￣ )")
    AuraEmotion.ANGRY     -> Face("( >_< )#", "( ˘_˘ )#", "( >o< )#")
    AuraEmotion.CONFUSED  -> Face("( •_• )?", "( -_- )?", "( •o• )?")
    AuraEmotion.SLEEPY    -> Face("( ˘_˘ )z", "( ˘_˘ )z", "( ˘o˘ )z")
    AuraEmotion.DIZZY     -> Face("( @_@ )", "( x_x )", "( @o@ )")
    AuraEmotion.PROUD     -> Face("( ˘◡˘ )", "( ˘‿˘ )", "( ˘o˘ )")
}

/** A short Vietnamese mood word for the sub-line beside the face. */
fun moodWord(emotion: AuraEmotion): String = when (emotion) {
    AuraEmotion.NEUTRAL   -> "bình thản"
    AuraEmotion.HAPPY     -> "đang vui"
    AuraEmotion.LOVE      -> "thương anh"
    AuraEmotion.LAUGH     -> "đang cười"
    AuraEmotion.SURPRISED -> "bất ngờ"
    AuraEmotion.SAD       -> "hơi buồn"
    AuraEmotion.THINKING  -> "đang ngẫm"
    AuraEmotion.WINK      -> "tinh nghịch"
    AuraEmotion.THANKS    -> "biết ơn"
    AuraEmotion.EXCITED   -> "hào hứng"
    AuraEmotion.SHY       -> "ngại ngùng"
    AuraEmotion.COOL      -> "thư thái"
    AuraEmotion.ANGRY     -> "hơi giận"
    AuraEmotion.CONFUSED  -> "bối rối"
    AuraEmotion.SLEEPY    -> "buồn ngủ"
    AuraEmotion.DIZZY     -> "quay quay"
    AuraEmotion.PROUD     -> "tự hào"
}

/** Map the reply's reaction emoji to an expression. Missing / unknown is a warm default. */
fun emotionFromReaction(emoji: String?): AuraEmotion {
    val e = emoji?.trim().orEmpty()
    return when {
        e.isEmpty() -> AuraEmotion.HAPPY
        e.any { it in "❤🥰😍💜🩷♥💕😘" } -> AuraEmotion.LOVE
        e.any { it in "🤩✨🎉🥳" } -> AuraEmotion.EXCITED
        e.any { it in "😂🤣😹😄😆" } -> AuraEmotion.LAUGH
        e.any { it in "😲😮😯🤯😧" } -> AuraEmotion.SURPRISED
        e.any { it in "😢😭😔🥺😞😟" } -> AuraEmotion.SAD
        e.any { it in "😉😜😏😝" } -> AuraEmotion.WINK
        e.any { it in "😳☺️🙈" } -> AuraEmotion.SHY
        e.any { it in "😎🆒" } -> AuraEmotion.COOL
        e.any { it in "😡😠🤬💢" } -> AuraEmotion.ANGRY
        e.any { it in "🤔😕🫤❓" } -> AuraEmotion.CONFUSED
        e.any { it in "😴😪💤" } -> AuraEmotion.SLEEPY
        e.any { it in "😵💫🥴" } -> AuraEmotion.DIZZY
        e.any { it in "👍🙏🙌🤝👌" } -> AuraEmotion.THANKS
        e.any { it in "😏💪🏆" } -> AuraEmotion.PROUD
        e.any { it in "🙂😊😌🥲" } -> AuraEmotion.HAPPY
        else -> AuraEmotion.HAPPY
    }
}

/** Everything the header presence row needs, derived from the whole chat state. */
data class AuraPresenceState(
    val emotion: AuraEmotion,
    val talking: Boolean,
    val status: String,
    val sub: String,
)

/**
 * What Aura is doing right now, in words, for the label beside her face.
 *
 * Reads through the same [ChatUiState] the screen renders, so the status line
 * and the conversation can never disagree about whether she is thinking.
 */
fun presenceOf(state: ChatUiState): AuraPresenceState {

    val streaming = state.messages.lastOrNull { it.streaming }
    if (streaming != null) {
        val e = emotionFromReaction(streaming.reactions["aura"])
        return AuraPresenceState(e, talking = true, status = "Đang nói…", sub = moodWord(e))
    }

    if (state.isAgentRunning) {
        return AuraPresenceState(
            AuraEmotion.THINKING, talking = false,
            status = "Đang thao tác…",
            sub = state.agentStatusText.ifBlank { "trên máy của anh" },
        )
    }

    if (state.isSending) {
        return AuraPresenceState(AuraEmotion.THINKING, talking = false, status = "Đang nghĩ…", sub = "chờ em xíu nha")
    }

    when (val c = state.connection) {
        is ConnectionState.Unavailable ->
            return AuraPresenceState(AuraEmotion.SAD, talking = false, status = "Ngoại tuyến", sub = c.reason)
        ConnectionState.Connecting ->
            return AuraPresenceState(AuraEmotion.NEUTRAL, talking = false, status = "Đang kết nối…", sub = "")
        ConnectionState.WakingUp ->
            return AuraPresenceState(AuraEmotion.NEUTRAL, talking = false, status = "Đang thức dậy…", sub = "server đang bật")
        is ConnectionState.OnDevice ->
            AuraPresenceState(AuraEmotion.HAPPY, false, "Sẵn sàng", c.modelName)
        else -> Unit
    }

    val lastAura = state.messages.lastOrNull { it.author == ChatMessage.Author.AURA }
    val e = emotionFromReaction(lastAura?.reactions?.get("aura"))
    val idleSub = when (val c = state.connection) {
        is ConnectionState.OnDevice -> c.modelName
        is ConnectionState.Connected -> c.provider
        else -> moodWord(e)
    }
    return AuraPresenceState(e, talking = false, status = "Sẵn sàng", sub = idleSub)
}

/**
 * The header: Aura's face in a soft glow, her status in words beside it.
 *
 * `( face        Đang nói… )` - the layout the owner sketched. The glow
 * breathes slowly and is tinted by the current emotion; it, and the blink and
 * the talking mouth, all stop under reduced motion.
 */
@Composable
fun AuraPresence(
    state: ChatUiState,
    modifier: Modifier = Modifier,
) {
    val presence = presenceOf(state)
    val accent = MaterialTheme.colorScheme.primary

    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 20.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {

        AuraFaceOrb(emotion = presence.emotion, talking = presence.talking, glow = accent)

        Spacer(Modifier.width(16.dp))

        Column {
            Text(
                text = presence.status,
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.onSurface,
            )
            if (presence.sub.isNotBlank()) {
                Text(
                    text = presence.sub,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

/** The face with a breathing radial glow behind it. */
@Composable
private fun AuraFaceOrb(
    emotion: AuraEmotion,
    talking: Boolean,
    glow: Color,
) {
    val reduced = rememberReducedMotion()

    val transition = rememberInfiniteTransition(label = "orb")
    val pulse by transition.animateFloat(
        initialValue = if (talking) 0.45f else 0.28f,
        targetValue = if (talking) 0.75f else 0.42f,
        animationSpec = infiniteRepeatable(
            animation = tween(if (talking) 900 else 2400),
            repeatMode = RepeatMode.Reverse,
        ),
        label = "orb-pulse",
    )
    val glowAlpha = if (reduced) 0.32f else pulse

    Box(contentAlignment = Alignment.Center) {

        // Halo: face-sized, scaled out and faded to a soft edge.
        Box(
            modifier = Modifier
                .matchParentSize()
                .graphicsLayer { scaleX = 1.9f; scaleY = 1.9f }
                .clip(CircleShape)
                .background(
                    Brush.radialGradient(
                        listOf(glow.copy(alpha = glowAlpha), Color.Transparent),
                    )
                )
        )

        KaomojiFace(
            emotion = emotion,
            talking = talking,
            style = MaterialTheme.typography.headlineMedium,
            color = glow,
            faceWidth = 156.dp,
        )
    }
}

/**
 * The face itself.
 *
 * @param talking mouth moves while true (a reply is arriving).
 */
@Composable
fun KaomojiFace(
    emotion: AuraEmotion,
    modifier: Modifier = Modifier,
    talking: Boolean = false,
    style: TextStyle = MaterialTheme.typography.headlineSmall,
    color: Color = LocalContentColor.current,
    faceWidth: Dp = 120.dp,
) {
    val reduced = rememberReducedMotion()
    val face = remember(emotion) { faceOf(emotion) }
    var frame by remember(emotion) { mutableStateOf(face.open) }

    LaunchedEffect(emotion, talking, reduced) {
        if (reduced) {
            frame = face.open
            return@LaunchedEffect
        }
        if (talking) {
            // A gentle two-beat mouth: open, closed, open… reads as speech
            // without a phoneme model behind it. Slow enough not to flicker.
            while (true) {
                frame = face.talk
                delay(260)
                frame = face.open
                delay(260)
            }
        } else {
            // Blink rarely and briefly. Same-width frames mean no layout jump.
            while (true) {
                frame = face.open
                delay(2800 + Random.nextLong(0, 2600))
                frame = face.blink
                delay(90)
            }
        }
    }

    // A small pop when the emotion changes, so a swap lands as a reaction
    // rather than a silent substitution. Held at 1f under reduced motion.
    val pop = remember { Animatable(1f) }
    LaunchedEffect(emotion) {
        if (reduced) return@LaunchedEffect
        pop.snapTo(1.14f)
        pop.animateTo(1f, animationSpec = tween(260))
    }

    // A FIXED-WIDTH box holds the face. Kaomoji glyphs (◕ ˘ ★ ￣ …) fall back to
    // a proportional font - Monospace does not actually cover them - so blinking
    // changes the text's pixel width and, without this, shoved whatever sat on
    // the same row. The box never changes width, so nothing beside it moves; the
    // text just re-centres within it. maxLines/softWrap keep it on one line.
    Box(
        modifier = modifier
            .width(faceWidth)
            .graphicsLayer { scaleX = pop.value; scaleY = pop.value },
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = frame,
            style = style.copy(fontFamily = FontFamily.Monospace),
            color = color,
            maxLines = 1,
            softWrap = false,
            textAlign = TextAlign.Center,
        )
    }
}
