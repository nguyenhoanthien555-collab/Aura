package com.aura.companion.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt

/**
 * Numeric and choice inputs for the Control Hub.
 *
 * BOUNDS COME FROM THE SERVER'S VALIDATORS
 * ----------------------------------------
 * Every caller passes the same range `core/settings_store.py` enforces -
 * `proactive.max_per_day` is 1..20 there and 1..20 here. Duplicating the
 * number is deliberate: the widget refuses to *produce* a value the server
 * would refuse to accept, so the common case never costs a round trip and
 * a 422. The server stays the authority; this is a courtesy, not a
 * substitute for it.
 *
 * Committing on release rather than on drag is what keeps that true of the
 * network too: a slider that PATCHed per frame would send forty requests
 * for one gesture.
 */

/**
 * A value picked from a range.
 *
 * @param onCommit fired once, when the user lets go.
 * @param format turns the raw number into what the row shows - "2 hours"
 *   reads better than "7200.0", and the unit is what the user thinks in.
 */
@Composable
fun SliderRow(
    title: String,
    value: Float,
    range: ClosedFloatingPointRange<Float>,
    onCommit: (Float) -> Unit,
    modifier: Modifier = Modifier,
    steps: Int = 0,
    subtitle: String? = null,
    enabled: Boolean = true,
    lockedReason: String? = null,
    format: (Float) -> String = { it.roundToInt().toString() },
) {
    val locked = lockedReason != null
    val interactive = enabled && !locked

    // Local while dragging, so the label tracks the thumb. Re-keyed on the
    // incoming value so a server response - or a reset - replaces it.
    var draft by remember(value) { mutableFloatStateOf(value) }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .padding(horizontal = 20.dp, vertical = 12.dp),
    ) {

        Row(verticalAlignment = Alignment.CenterVertically) {

            Column(modifier = Modifier.weight(1f)) {

                Text(text = title, style = MaterialTheme.typography.bodyLarge)

                if (locked || !subtitle.isNullOrBlank()) {
                    Spacer(Modifier.height(2.dp))
                    Text(
                        text = lockedReason ?: subtitle.orEmpty(),
                        style = MaterialTheme.typography.bodySmall,
                        color = if (locked) {
                            MaterialTheme.colorScheme.tertiary
                        } else {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        },
                    )
                }
            }

            Text(
                text = format(draft),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.primary,
                fontWeight = FontWeight.SemiBold,
            )
        }

        Slider(
            value = draft.coerceIn(range),
            onValueChange = { draft = it },
            onValueChangeFinished = { onCommit(draft) },
            valueRange = range,
            steps = steps,
            enabled = interactive,
        )
    }
}

/**
 * A whole number with plus and minus.
 *
 * Preferred over a slider for small ranges - picking "4 a day" out of
 * 1..20 by dragging is fiddly, and the buttons cannot overshoot.
 */
@Composable
fun StepperRow(
    title: String,
    value: Int,
    range: IntRange,
    onCommit: (Int) -> Unit,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    enabled: Boolean = true,
    lockedReason: String? = null,
    format: (Int) -> String = { it.toString() },
) {
    val locked = lockedReason != null
    val interactive = enabled && !locked

    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = modifier
            .fillMaxWidth()
            .padding(start = 20.dp, end = 8.dp, top = 12.dp, bottom = 12.dp),
    ) {

        Column(modifier = Modifier.weight(1f)) {

            Text(text = title, style = MaterialTheme.typography.bodyLarge)

            if (locked || !subtitle.isNullOrBlank()) {
                Spacer(Modifier.height(2.dp))
                Text(
                    text = lockedReason ?: subtitle.orEmpty(),
                    style = MaterialTheme.typography.bodySmall,
                    color = if (locked) {
                        MaterialTheme.colorScheme.tertiary
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                )
            }
        }

        IconButton(
            onClick = { onCommit((value - 1).coerceIn(range)) },
            enabled = interactive && value > range.first,
        ) {
            Icon(Icons.Filled.Remove, contentDescription = "Decrease $title")
        }

        Text(
            text = format(value),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.primary,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.width(64.dp),
        )

        IconButton(
            onClick = { onCommit((value + 1).coerceIn(range)) },
            enabled = interactive && value < range.last,
        ) {
            Icon(Icons.Filled.Add, contentDescription = "Increase $title")
        }
    }
}

/**
 * Pick one of a list.
 *
 * Scrollable and height-bounded because a provider's model list is not
 * guaranteed short, and a dialog taller than the screen has an OK button
 * nobody can reach.
 */
@Composable
fun ChoiceDialog(
    title: String,
    options: List<String>,
    selected: String,
    onPick: (String) -> Unit,
    onDismiss: () -> Unit,
    labels: (String) -> String = { it },
    emptyMessage: String = "Nothing to choose from.",
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            if (options.isEmpty()) {
                Text(emptyMessage)
            } else {
                Column(
                    modifier = Modifier
                        .heightIn(max = 380.dp)
                        .verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    options.forEach { option ->
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier
                                .fillMaxWidth()
                                .selectable(
                                    selected = option == selected,
                                    role = Role.RadioButton,
                                    onClick = {
                                        onPick(option)
                                        onDismiss()
                                    },
                                )
                                .padding(vertical = 10.dp),
                        ) {
                            RadioButton(
                                selected = option == selected,
                                onClick = null,
                            )
                            Spacer(Modifier.width(12.dp))
                            Text(
                                text = labels(option),
                                style = MaterialTheme.typography.bodyLarge,
                            )
                        }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) { Text("Close") }
        },
    )
}

/**
 * Type a value the server will validate.
 *
 * Used for the free-text settings - a model name Aura's built-in list does
 * not know about. The server's `_non_empty_text` validator is the check
 * that matters; this only refuses to submit blanks, because a blank is the
 * one mistake worth catching before the round trip.
 */
@Composable
fun TextEntryDialog(
    title: String,
    initial: String,
    onCommit: (String) -> Unit,
    onDismiss: () -> Unit,
    label: String = "Value",
    help: String? = null,
) {
    var draft by remember { mutableStateOf(initial) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = {
            Column {
                if (help != null) {
                    Text(
                        text = help,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.height(12.dp))
                }
                OutlinedTextField(
                    value = draft,
                    onValueChange = { draft = it },
                    label = { Text(label) },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    onCommit(draft.trim())
                    onDismiss()
                },
                enabled = draft.isNotBlank(),
            ) {
                Text("Save")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}
