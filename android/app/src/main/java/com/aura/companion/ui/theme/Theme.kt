package com.aura.companion.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aura.companion.data.settings.ThemeMode

/**
 * Aura's colours.
 *
 * The identity is indigo-violet on a deep, cool near-black - the palette the
 * owner signed off on. Violet is the voice (Aura herself), indigo the
 * structure (surfaces, structure, the app around her), and a small cyan
 * accent is reserved for "live / good / connected". They are deliberately not
 * saturated to the edge: this is an app open late at night beside something
 * the owner is actually doing, and a companion that glows should read as
 * depth, not as the brightest thing in the room.
 */
private val AuraViolet = Color(0xFF8B5CF6)      // the accent / Aura's voice
private val AuraVioletDeep = Color(0xFF4C1D95)  // her container
private val AuraIndigo = Color(0xFF6366F1)      // structure
private val AuraIndigoDeep = Color(0xFF3730A3)
private val AuraCyan = Color(0xFF22D3EE)         // "live"

private val DarkColors = darkColorScheme(
    primary = AuraViolet,
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = AuraVioletDeep,
    onPrimaryContainer = Color(0xFFEDE9FE),
    secondary = AuraIndigo,
    onSecondary = Color(0xFFFFFFFF),
    secondaryContainer = AuraIndigoDeep,
    onSecondaryContainer = Color(0xFFE0E7FF),
    tertiary = AuraCyan,
    onTertiary = Color(0xFF00363F),
    tertiaryContainer = Color(0xFF0E7490),
    onTertiaryContainer = Color(0xFFCFFAFE),
    background = Color(0xFF0B0F19),
    onBackground = Color(0xFFE5E7EB),
    surface = Color(0xFF111827),
    onSurface = Color(0xFFF3F4F6),
    surfaceVariant = Color(0xFF1F2937),
    onSurfaceVariant = Color(0xFFC7CBD4),
    surfaceContainerLowest = Color(0xFF080B12),
    surfaceContainerLow = Color(0xFF0F1521),
    surfaceContainer = Color(0xFF141B29),
    surfaceContainerHigh = Color(0xFF1B2333),
    surfaceContainerHighest = Color(0xFF232C3E),
    outline = Color(0xFF3A4457),
    outlineVariant = Color(0xFF232C3E),
    inverseSurface = Color(0xFFE5E7EB),
    inverseOnSurface = Color(0xFF1B2333),
    error = Color(0xFFF87171),
    onError = Color(0xFF450A0A),
    errorContainer = Color(0xFF7F1D1D),
    onErrorContainer = Color(0xFFFEE2E2),
)

private val LightColors = lightColorScheme(
    primary = Color(0xFF6D28D9),
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFEDE4FF),
    onPrimaryContainer = Color(0xFF23104D),
    secondary = Color(0xFF4F46E5),
    onSecondary = Color(0xFFFFFFFF),
    secondaryContainer = Color(0xFFE0E7FF),
    onSecondaryContainer = Color(0xFF1E1B4B),
    tertiary = Color(0xFF0891B2),
    onTertiary = Color(0xFFFFFFFF),
    tertiaryContainer = Color(0xFFCFFAFE),
    onTertiaryContainer = Color(0xFF083344),
    background = Color(0xFFFBFAFF),
    onBackground = Color(0xFF16181D),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF16181D),
    surfaceVariant = Color(0xFFECEAF4),
    onSurfaceVariant = Color(0xFF474656),
    outline = Color(0xFFC5C2D4),
    outlineVariant = Color(0xFFE3E0ED),
    error = Color(0xFFDC2626),
    onError = Color(0xFFFFFFFF),
    errorContainer = Color(0xFFFEE2E2),
    onErrorContainer = Color(0xFF7F1D1D),
)

/**
 * The type scale.
 *
 * A fuller ramp than before, because a rebuilt UI needs real hierarchy - a
 * screen title, a section header, a body line and a caption should each be
 * unmistakably a different rank, and four styles could not carry that. Line
 * heights are generous: Vietnamese stacks diacritics above and below the
 * baseline, and a tight leading clips them.
 */
private val AuraTypography = Typography(
    displaySmall = TextStyle(fontSize = 34.sp, lineHeight = 42.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.5).sp),
    headlineMedium = TextStyle(fontSize = 26.sp, lineHeight = 34.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.25).sp),
    headlineSmall = TextStyle(fontSize = 22.sp, lineHeight = 30.sp, fontWeight = FontWeight.SemiBold),
    titleLarge = TextStyle(fontSize = 19.sp, lineHeight = 26.sp, fontWeight = FontWeight.SemiBold),
    titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 23.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 0.1.sp),
    titleSmall = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.Medium),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 24.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 21.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 17.sp, color = Color.Unspecified),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.Medium),
    labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.4.sp),
    labelSmall = TextStyle(fontSize = 11.sp, lineHeight = 15.sp, fontWeight = FontWeight.Medium, letterSpacing = 0.4.sp),
)

/**
 * Corner radii.
 *
 * Softer and larger than Material's defaults: the rebuild leans on rounded,
 * pill-ish surfaces for the warm companion feel, so a card is 20dp and a chip
 * is a near-full pill rather than the stock 8/12.
 */
private val AuraShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(16.dp),
    large = RoundedCornerShape(22.dp),
    extraLarge = RoundedCornerShape(28.dp),
)

/**
 * Wraps the app, honouring the user's appearance settings.
 *
 * [ThemeMode.System] resolves to the platform's own answer, which is what
 * keeps "Follow system" honest when the phone switches at sunset.
 *
 * `dynamicColour` is a user setting rather than a hard-coded `true` because
 * on Android 12+ wallpaper colour *wins* over the palette below - so
 * without a way to turn it off there is no way to actually see Aura's own
 * violet.
 */
@Composable
fun AuraTheme(
    themeMode: ThemeMode,
    dynamicColour: Boolean,
    content: @Composable () -> Unit,
) {
    AuraTheme(
        dark = when (themeMode) {
            ThemeMode.System -> isSystemInDarkTheme()
            ThemeMode.Light -> false
            ThemeMode.Dark -> true
        },
        dynamic = dynamicColour,
        content = content,
    )
}

/**
 * Wraps the app.
 *
 * Dynamic colour is used where the platform offers it (Android 12+) *and* the
 * user has left it on, so Aura can match the wallpaper. The hand-picked
 * indigo-violet scheme above is both the fallback and, with dynamic colour
 * switched off, Aura's own face.
 */
@Composable
fun AuraTheme(
    dark: Boolean = isSystemInDarkTheme(),
    dynamic: Boolean = true,
    content: @Composable () -> Unit,
) {

    val context = LocalContext.current

    val colors = when {
        dynamic && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S ->
            if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)

        dark -> DarkColors
        else -> LightColors
    }

    MaterialTheme(
        colorScheme = colors,
        typography = AuraTypography,
        shapes = AuraShapes,
        content = content,
    )
}
