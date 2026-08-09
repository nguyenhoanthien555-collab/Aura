package com.aura.companion.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.unit.sp

/**
 * Aura's colours.
 *
 * A muted violet rather than a saturated brand colour: this is an app
 * someone has open late at night, and a companion that glows should not be
 * the brightest thing in the room.
 */
private val AuraViolet = Color(0xFF7C6BF2)
private val AuraVioletDark = Color(0xFF5B4CD6)
private val AuraTeal = Color(0xFF4FC3B0)

private val DarkColors = darkColorScheme(
    primary = AuraViolet,
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = AuraVioletDark,
    onPrimaryContainer = Color(0xFFEDEAFF),
    secondary = AuraTeal,
    onSecondary = Color(0xFF00382F),
    background = Color(0xFF121016),
    onBackground = Color(0xFFE6E1E9),
    surface = Color(0xFF1A1720),
    onSurface = Color(0xFFE6E1E9),
    surfaceVariant = Color(0xFF2A2533),
    onSurfaceVariant = Color(0xFFC9C3D4),
    error = Color(0xFFFFB4AB),
    onError = Color(0xFF690005),
)

private val LightColors = lightColorScheme(
    primary = AuraVioletDark,
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFE6E0FF),
    onPrimaryContainer = Color(0xFF1B1046),
    secondary = Color(0xFF00695C),
    onSecondary = Color(0xFFFFFFFF),
    background = Color(0xFFFCFAFF),
    onBackground = Color(0xFF1B1B1F),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF1B1B1F),
    surfaceVariant = Color(0xFFE7E0EC),
    onSurfaceVariant = Color(0xFF49454F),
)

private val AuraTypography = Typography(
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 23.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    titleMedium = TextStyle(fontSize = 17.sp, fontWeight = FontWeight.SemiBold),
    labelSmall = TextStyle(fontSize = 11.sp, lineHeight = 15.sp),
)

/**
 * Wraps the app.
 *
 * Dynamic colour is used where the platform offers it (Android 12+), so
 * Aura matches the user's wallpaper rather than insisting on its own
 * palette. The hand-picked scheme above is the fallback.
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
        content = content,
    )
}
