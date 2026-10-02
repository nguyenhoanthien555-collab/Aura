package com.aura.companion.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.aura.companion.ui.hub.HubRoutes
import com.aura.companion.ui.theme.AuraIcons
import com.aura.companion.ui.theme.auraGlassBlur

/**
 * Aura's ergonomic bottom Cyber Dock.
 *
 * Provides instant 1-tap switching between Chat, Memory Hub, Tools/Control Hub,
 * and System Diagnostics without requiring users to navigate multiple nested
 * settings layers.
 *
 * Automatically and seamlessly tucks away when the soft keyboard appears to
 * preserve maximum typing canvas.
 */
@Composable
fun AuraCyberDock(
    currentRoute: String,
    onNavigate: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val density = LocalDensity.current
    val isKeyboardOpen = WindowInsets.ime.getBottom(density) > 0

    AnimatedVisibility(
        visible = !isKeyboardOpen,
        enter = fadeIn() + expandVertically(expandFrom = Alignment.Bottom),
        exit = fadeOut() + shrinkVertically(shrinkTowards = Alignment.Bottom),
        modifier = modifier,
    ) {
        val shape = RoundedCornerShape(26.dp)

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 14.dp, vertical = 6.dp)
                .auraGlassBlur(
                    shape = shape,
                    tint = MaterialTheme.colorScheme.surface.copy(alpha = 0.88f),
                    elevation = 6.dp,
                )
                .clip(shape),
        ) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 6.dp, vertical = 6.dp),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                DockTabItem(
                    label = "Trò chuyện",
                    icon = AuraIcons.ChatBubble,
                    selected = currentRoute == "chat" || currentRoute.isEmpty(),
                    onClick = { onNavigate("chat") },
                    modifier = Modifier.weight(1f),
                )
                DockTabItem(
                    label = "Báo thức",
                    icon = AuraIcons.Alarm,
                    selected = currentRoute == HubRoutes.ALARMS,
                    onClick = { onNavigate(HubRoutes.ALARMS) },
                    modifier = Modifier.weight(1f),
                )
                DockTabItem(
                    label = "Trí nhớ",
                    icon = AuraIcons.KnowledgeGraph,
                    selected = currentRoute == HubRoutes.MEMORY,
                    onClick = { onNavigate(HubRoutes.MEMORY) },
                    modifier = Modifier.weight(1f),
                )
                DockTabItem(
                    label = "Công cụ",
                    icon = AuraIcons.Build,
                    selected = currentRoute == HubRoutes.HUB || currentRoute == HubRoutes.TOOLS,
                    onClick = { onNavigate(HubRoutes.HUB) },
                    modifier = Modifier.weight(1f),
                )
                DockTabItem(
                    label = "Hệ thống",
                    icon = AuraIcons.MonitorHeart,
                    selected = currentRoute == HubRoutes.DIAGNOSTICS || currentRoute == HubRoutes.AURA,
                    onClick = { onNavigate(HubRoutes.DIAGNOSTICS) },
                    modifier = Modifier.weight(1f),
                )
            }
        }
    }
}

@Composable
private fun DockTabItem(
    label: String,
    icon: ImageVector,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val haptic = LocalHapticFeedback.current
    val shape = RoundedCornerShape(20.dp)

    val contentColor by animateColorAsState(
        targetValue = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.65f),
        label = "dockTabColor",
    )

    val backgroundAlpha by animateFloatAsState(
        targetValue = if (selected) 0.16f else 0.0f,
        label = "dockTabBgAlpha",
    )

    val indicatorHeight by animateDpAsState(
        targetValue = if (selected) 3.dp else 0.dp,
        animationSpec = spring(
            dampingRatio = Spring.DampingRatioMediumBouncy,
            stiffness = Spring.StiffnessMediumLow,
        ),
        label = "dockIndicatorHeight",
    )

    Column(
        modifier = modifier
            .clip(shape)
            .background(
                color = MaterialTheme.colorScheme.primary.copy(alpha = backgroundAlpha),
                shape = shape,
            )
            .clickable(
                interactionSource = remember { MutableInteractionSource() },
                indication = null,
                onClick = {
                    haptic.performHapticFeedback(HapticFeedbackType.TextHandleMove)
                    onClick()
                },
            )
            .padding(vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            imageVector = icon,
            contentDescription = label,
            tint = contentColor,
            modifier = Modifier.size(20.dp),
        )

        Spacer(modifier = Modifier.height(2.dp))

        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall.copy(
                fontSize = 11.sp,
                fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            ),
            color = contentColor,
            maxLines = 1,
        )

        Spacer(modifier = Modifier.height(2.dp))

        // Glowing indicator pill under selected tab
        Box(
            modifier = Modifier
                .size(width = 16.dp, height = indicatorHeight)
                .background(
                    brush = Brush.horizontalGradient(
                        listOf(
                            MaterialTheme.colorScheme.primary.copy(alpha = 0.6f),
                            MaterialTheme.colorScheme.tertiary,
                            MaterialTheme.colorScheme.primary.copy(alpha = 0.6f),
                        )
                    ),
                    shape = CircleShape,
                ),
        )
    }
}
