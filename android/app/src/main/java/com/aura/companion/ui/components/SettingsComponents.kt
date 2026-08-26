package com.aura.companion.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.expandVertically
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkVertically
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.theme.AuraMotion
import com.aura.companion.ui.theme.auraGlassEdge
import com.aura.companion.ui.theme.auraGlassBlur
import com.aura.companion.ui.theme.rememberReducedMotion

/**
 * The Control Hub's building blocks.
 *
 * Every settings screen is assembled from these rather than from raw
 * `Row`/`Switch`/`Text`. That is not tidiness for its own sake: a toggle
 * that is disabled because the server needs a restart, one that is
 * disabled because an Android permission is missing, and one that is off
 * because the user turned it off have to *look* different, and getting
 * that right once is the only way it stays right in eleven places.
 *
 * DISABLED IS NEVER SILENT
 * ------------------------
 * [ToggleRow] and [SelectRow] take a `lockedReason`. A control the user
 * cannot move always says why, in the row, because a greyed-out switch
 * with no explanation is indistinguishable from a bug.
 *
 * ANIMATION BUDGET
 * ----------------
 * Section expansion, the toggle itself, and a fade on status changes.
 * Nothing else moves. A settings screen that animates on every scroll
 * frame is slower to read, not nicer.
 *
 * The durations come from [AuraMotion] and are scaled by
 * [rememberReducedMotion], so these read as the same app as the hub's hero
 * card and honour "Remove animations" without a setting of Aura's own.
 * They used to be three literals - 150, 180, 120 - which is how a second
 * design system starts.
 */

// ----------------------------------------------------------------------
// Structure
// ----------------------------------------------------------------------

/**
 * A titled group of related settings.
 *
 * The card is the unit of visual grouping - a rounded surface a shade off
 * the background, no elevation. Elevation on a scrolling list of eleven
 * cards is visual noise; a tonal step reads as grouping without it.
 */
@Composable
fun SettingsSection(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    content: @Composable () -> Unit,
) {
    Column(modifier = modifier.fillMaxWidth()) {

        SectionHeader(title = title, subtitle = subtitle)

        SettingsCard { content() }
    }
}

@Composable
fun SectionHeader(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
) {
    Column(
        modifier = modifier.padding(start = 4.dp, top = 20.dp, bottom = 8.dp),
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.primary,
            fontWeight = FontWeight.SemiBold,
        )

        if (subtitle != null) {
            Spacer(Modifier.height(2.dp))
            Text(
                text = subtitle,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/**
 * The rounded surface every group of rows sits on.
 *
 * Carries the hub's own hairline edge ([auraGlassEdge]) rather than an
 * elevation shadow: the front page's tiles are already glass, and a settings
 * card that was a flat tonal block read as a different app one tap in.
 */
@Composable
fun SettingsCard(
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    val shape = RoundedCornerShape(28.dp)

    Box(
        modifier = modifier
            .fillMaxWidth()
            .auraGlassBlur(shape),
    ) {
        Column(modifier = Modifier.padding(vertical = 8.dp)) { content() }
    }
}

/** A hairline between rows inside a card. Inset, so it reads as a group. */
@Composable
fun RowDivider() {
    Box(
        modifier = Modifier
            .padding(start = 20.dp, end = 20.dp)
            .fillMaxWidth()
            .height(1.dp)
            .background(MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.35f)),
    )
}

// ----------------------------------------------------------------------
// Rows
// ----------------------------------------------------------------------

/**
 * A setting with a switch.
 *
 * @param lockedReason when non-null the switch is inert and this is shown
 *   in place of the subtitle. This is the mechanism that keeps the Control
 *   Hub honest: a toggle whose subsystem is not running says so.
 * @param pending true while the change is in flight to the server. The row
 *   shows the value the user chose and a spinner - optimism with a visible
 *   caveat, which is better than a switch that snaps back a second later.
 */
@Composable
fun ToggleRow(
    title: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    icon: ImageVector? = null,
    enabled: Boolean = true,
    lockedReason: String? = null,
    pending: Boolean = false,
) {
    val locked = lockedReason != null
    val interactive = enabled && !locked && !pending

    SettingRowFrame(
        title = title,
        subtitle = lockedReason ?: subtitle,
        icon = icon,
        dimmed = !interactive,
        subtitleIsWarning = locked,
        modifier = modifier,
        trailing = {
            if (pending) {
                CircularProgressIndicator(
                    modifier = Modifier.size(20.dp),
                    strokeWidth = 2.dp,
                )
                Spacer(Modifier.width(8.dp))
            }

            Switch(
                checked = checked,
                onCheckedChange = if (interactive) onCheckedChange else null,
                enabled = interactive,
            )
        },
    )
}

/**
 * A setting whose value is chosen elsewhere - a sheet, a dialog, a screen.
 *
 * The value is shown on the row so the section can be read without opening
 * anything, which is what stops the hub from becoming eleven screens deep.
 */
@Composable
fun SelectRow(
    title: String,
    value: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    icon: ImageVector? = null,
    enabled: Boolean = true,
    lockedReason: String? = null,
) {
    val locked = lockedReason != null
    val interactive = enabled && !locked

    SettingRowFrame(
        title = title,
        subtitle = lockedReason ?: subtitle,
        icon = icon,
        dimmed = !interactive,
        subtitleIsWarning = locked,
        onClick = if (interactive) onClick else null,
        modifier = modifier,
        trailing = {
            Text(
                text = value,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                // Bounded so a long model name truncates instead of
                // pushing the chevron off the row.
                modifier = Modifier.widthIn(max = 168.dp),
            )
            Spacer(Modifier.width(4.dp))
            Icon(
                imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(20.dp),
            )
        },
    )
}

/** Read-only. A fact about the server, not a control. */
@Composable
fun StatusRow(
    title: String,
    value: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    tone: StatusTone = StatusTone.Neutral,
    icon: ImageVector? = null,
) {
    SettingRowFrame(
        title = title,
        subtitle = subtitle,
        icon = icon,
        modifier = modifier,
        trailing = {
            if (tone != StatusTone.Neutral) {
                StatusDot(tone)
                Spacer(Modifier.width(8.dp))
            }
            Text(
                text = value,
                style = MaterialTheme.typography.bodyMedium,
                color = tone.contentColour(),
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
        },
    )
}

/**
 * A row that navigates somewhere.
 *
 * The hub's top level is nothing but these, which is what STEP 2 means by
 * "do not put every setting on the home screen".
 */
@Composable
fun NavigationRow(
    title: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    icon: ImageVector? = null,
    badge: String? = null,
    badgeTone: StatusTone = StatusTone.Neutral,
) {
    SettingRowFrame(
        title = title,
        subtitle = subtitle,
        icon = icon,
        onClick = onClick,
        modifier = modifier,
        trailing = {
            if (!badge.isNullOrBlank()) {
                Badge(text = badge, tone = badgeTone)
                Spacer(Modifier.width(8.dp))
            }
            Icon(
                imageVector = Icons.AutoMirrored.Filled.KeyboardArrowRight,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(20.dp),
            )
        },
    )
}

/**
 * The shared shape of every row above.
 *
 * One implementation of the 56dp-minimum touch target, the icon slot, the
 * two-line text block and the dimming rule. Rows differ only in what sits
 * on the right.
 */
@Composable
private fun SettingRowFrame(
    title: String,
    subtitle: String?,
    icon: ImageVector?,
    modifier: Modifier = Modifier,
    dimmed: Boolean = false,
    subtitleIsWarning: Boolean = false,
    onClick: (() -> Unit)? = null,
    trailing: @Composable () -> Unit,
) {
    val alpha by animateFloatAsState(
        targetValue = if (dimmed) 0.55f else 1f,
        animationSpec = tween(
            durationMillis = AuraMotion.scaled(AuraMotion.Quick, rememberReducedMotion()),
        ),
        label = "rowAlpha",
    )

    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = modifier
            .fillMaxWidth()
            .then(
                if (onClick != null) {
                    Modifier.clickable(role = Role.Button, onClick = onClick)
                } else {
                    Modifier
                }
            )
            .padding(horizontal = 20.dp, vertical = 14.dp)
            .alpha(alpha),
    ) {

        if (icon != null) {
            Icon(
                imageVector = icon,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(22.dp),
            )
            Spacer(Modifier.width(16.dp))
        }

        Column(modifier = Modifier.weight(1f)) {

            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
            )

            if (!subtitle.isNullOrBlank()) {
                Spacer(Modifier.height(2.dp))
                Text(
                    text = subtitle,
                    style = MaterialTheme.typography.bodySmall,
                    color = if (subtitleIsWarning) {
                        MaterialTheme.colorScheme.tertiary
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
            }
        }

        Spacer(Modifier.width(12.dp))

        Row(verticalAlignment = Alignment.CenterVertically) { trailing() }
    }
}

// ----------------------------------------------------------------------
// Status vocabulary
// ----------------------------------------------------------------------

/**
 * The four states a capability can be in.
 *
 * STEP 12 requires these to be distinguishable, and they are genuinely
 * different situations with different remedies:
 *
 *   Good        working
 *   Warning     needs something from the user (a permission, a restart)
 *   Bad         broken or refused
 *   Neutral     off, or not applicable
 */
enum class StatusTone { Good, Warning, Bad, Neutral }

@Composable
fun StatusTone.contentColour(): Color = when (this) {
    StatusTone.Good -> MaterialTheme.colorScheme.primary
    StatusTone.Warning -> MaterialTheme.colorScheme.tertiary
    StatusTone.Bad -> MaterialTheme.colorScheme.error
    StatusTone.Neutral -> MaterialTheme.colorScheme.onSurfaceVariant
}

@Composable
fun StatusDot(tone: StatusTone, modifier: Modifier = Modifier, size: Dp = 8.dp) {
    Box(
        modifier = modifier
            .size(size)
            .background(tone.contentColour(), CircleShape),
    )
}

@Composable
fun Badge(text: String, tone: StatusTone = StatusTone.Neutral) {
    Surface(
        shape = RoundedCornerShape(8.dp),
        color = tone.contentColour().copy(alpha = 0.14f),
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.labelSmall,
            color = tone.contentColour(),
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
        )
    }
}

// ----------------------------------------------------------------------
// Feedback
// ----------------------------------------------------------------------

/**
 * A short message about what just happened, or what is wrong.
 *
 * Used for the honest half of the Control Hub: "saved, restart Aura for
 * this to take effect", "this key will not survive a restart".
 */
@Composable
fun NoticeCard(
    text: String,
    modifier: Modifier = Modifier,
    tone: StatusTone = StatusTone.Neutral,
    icon: ImageVector? = null,
) {
    val shape = RoundedCornerShape(20.dp)

    Box(
        modifier = modifier
            .fillMaxWidth()
            .auraGlassBlur(shape, tint = tone.contentColour().copy(alpha = 0.15f)),
    ) {
        Row(
            verticalAlignment = Alignment.Top,
            modifier = Modifier.padding(14.dp),
        ) {
            if (icon != null) {
                Icon(
                    imageVector = icon,
                    contentDescription = null,
                    tint = tone.contentColour(),
                    modifier = Modifier.size(18.dp),
                )
                Spacer(Modifier.width(10.dp))
            }
            Text(
                text = text,
                style = MaterialTheme.typography.bodySmall,
                color = tone.contentColour(),
            )
        }
    }
}

/** [NoticeCard], appearing and leaving without a jump. */
@Composable
fun AnimatedNotice(
    text: String?,
    modifier: Modifier = Modifier,
    tone: StatusTone = StatusTone.Neutral,
    icon: ImageVector? = null,
) {
    val reduced = rememberReducedMotion()

    // Arriving is the half worth seeing - it is new information. Leaving is
    // quicker, because a notice the user has finished with should get out of
    // the way rather than be seen out.
    val arrive = AuraMotion.scaled(AuraMotion.Standard, reduced)
    val leave = AuraMotion.scaled(AuraMotion.Quick, reduced)

    AnimatedVisibility(
        visible = !text.isNullOrBlank(),
        enter = fadeIn(tween(arrive)) + expandVertically(tween(arrive)),
        exit = fadeOut(tween(leave)) + shrinkVertically(tween(leave)),
    ) {
        Column {
            NoticeCard(
                text = text.orEmpty(),
                tone = tone,
                icon = icon,
                modifier = modifier,
            )
            Spacer(Modifier.height(12.dp))
        }
    }
}

/**
 * A group that can be collapsed.
 *
 * STEP 14 puts debug and advanced controls behind one of these: present
 * for the person who needs them, not in the way of the person who does
 * not.
 */
@Composable
fun ExpandableSection(
    title: String,
    expanded: Boolean,
    onExpandedChange: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    content: @Composable () -> Unit,
) {
    val reduced = rememberReducedMotion()

    val arrive = AuraMotion.scaled(AuraMotion.Standard, reduced)
    val leave = AuraMotion.scaled(AuraMotion.Quick, reduced)

    Column(modifier = modifier.fillMaxWidth()) {

        Row(
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.Start,
            modifier = Modifier
                .fillMaxWidth()
                .clickable(role = Role.Button) { onExpandedChange(!expanded) }
                .padding(start = 4.dp, top = 20.dp, bottom = 8.dp),
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                    fontWeight = FontWeight.SemiBold,
                )
                if (subtitle != null) {
                    Spacer(Modifier.height(2.dp))
                    Text(
                        text = subtitle,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            Icon(
                imageVector = if (expanded) {
                    Icons.Filled.KeyboardArrowDown
                } else {
                    Icons.AutoMirrored.Filled.KeyboardArrowRight
                },
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        AnimatedVisibility(
            visible = expanded,
            enter = fadeIn(tween(arrive)) + expandVertically(tween(arrive)),
            exit = fadeOut(tween(leave)) + shrinkVertically(tween(leave)),
        ) {
            SettingsCard { content() }
        }
    }
}
