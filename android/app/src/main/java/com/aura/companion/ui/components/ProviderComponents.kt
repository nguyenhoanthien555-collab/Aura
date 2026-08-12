package com.aura.companion.ui.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp

/**
 * Provider, model and API-key UI.
 *
 * WHAT THIS FILE IS CAREFUL ABOUT
 * ------------------------------
 * [ApiKeyField] is the only place in the app where an API key exists as a
 * value, and it exists there for as long as it takes to press Save. It is
 * held in a `remember { mutableStateOf }` inside the composable, cleared
 * the moment the caller reports success, masked on screen, and never
 * written to the device's own settings store. The mask the server returns
 * is displayed as a *label*, never as the field's contents - so pressing
 * Save without typing cannot post a row of bullets back as if it were a
 * key. (The server rejects that too; both ends refuse.)
 *
 * Capability chips render what the server reported about its own
 * implementation. A provider whose class has no `stream()` shows no
 * streaming chip, whatever the vendor's API can do.
 */

// ----------------------------------------------------------------------
// Provider
// ----------------------------------------------------------------------

/**
 * One provider: what it can do, whether it has a key, and its role.
 *
 * @param onTest sends one real prompt and costs a token or two, hence a
 *   button rather than something that happens on scroll.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ProviderCard(
    label: String,
    modifier: Modifier = Modifier,
    capabilities: List<String> = emptyList(),
    /**
     * Model, endpoint and health, from [providerFacts].
     *
     * Passed in rather than derived here so the wording is under unit test -
     * this module has no JVM Compose harness, so nothing asserted can live
     * inside a `@Composable`.
     */
    facts: List<String> = emptyList(),
    configured: Boolean = false,
    keyless: Boolean = false,
    keyMasked: String = "",
    keySource: String = "",
    isPrimary: Boolean = false,
    isFallback: Boolean = false,
    testing: Boolean = false,
    testResult: String? = null,
    testOk: Boolean = false,
    onTest: (() -> Unit)? = null,
    onSetPrimary: (() -> Unit)? = null,
    onToggleFallback: (() -> Unit)? = null,
    onManageKey: (() -> Unit)? = null,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
    ) {
        Column(modifier = Modifier.padding(18.dp)) {

            Row(verticalAlignment = Alignment.CenterVertically) {

                Column(modifier = Modifier.weight(1f)) {

                    Text(
                        text = label,
                        style = MaterialTheme.typography.titleMedium,
                    )

                    Spacer(Modifier.height(3.dp))

                    Text(
                        text = keyStateText(configured, keyless, keyMasked, keySource),
                        style = MaterialTheme.typography.bodySmall,
                        color = if (configured) {
                            MaterialTheme.colorScheme.onSurfaceVariant
                        } else {
                            MaterialTheme.colorScheme.tertiary
                        },
                    )
                }

                if (isPrimary) Badge("Primary", StatusTone.Good)

                if (isFallback && !isPrimary) Badge("Fallback", StatusTone.Neutral)
            }

            if (facts.isNotEmpty()) {

                Spacer(Modifier.height(10.dp))

                Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
                    facts.forEach { fact ->
                        Text(
                            text = fact,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant
                                .copy(alpha = 0.75f),
                        )
                    }
                }
            }

            if (capabilities.isNotEmpty()) {

                Spacer(Modifier.height(12.dp))

                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    capabilities.forEach { Badge(it, StatusTone.Neutral) }
                }
            }

            AnimatedVisibility(
                visible = testResult != null,
                enter = fadeIn(tween(160)),
                exit = fadeOut(tween(120)),
            ) {
                Column {
                    Spacer(Modifier.height(12.dp))
                    NoticeCard(
                        text = testResult.orEmpty(),
                        tone = if (testOk) StatusTone.Good else StatusTone.Bad,
                    )
                }
            }

            Spacer(Modifier.height(14.dp))

            FlowRow(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {

                if (onTest != null) {
                    OutlinedButton(
                        onClick = onTest,
                        enabled = !testing && configured,
                    ) {
                        if (testing) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(14.dp),
                                strokeWidth = 2.dp,
                            )
                            Spacer(Modifier.width(8.dp))
                        }
                        Text("Test")
                    }
                }

                if (onSetPrimary != null && !isPrimary) {
                    OutlinedButton(onClick = onSetPrimary, enabled = configured) {
                        Text("Set primary")
                    }
                }

                if (onToggleFallback != null && !isPrimary) {
                    OutlinedButton(onClick = onToggleFallback, enabled = configured) {
                        Text(if (isFallback) "Remove fallback" else "Add fallback")
                    }
                }

                if (onManageKey != null && !keyless) {
                    OutlinedButton(onClick = onManageKey) {
                        Text(if (configured) "Change key" else "Add key")
                    }
                }
            }
        }
    }
}

/**
 * Where this provider's key comes from, in words.
 *
 * The distinction matters for what the user can do next: a key from the
 * deployment's environment cannot be deleted from a phone, and saying so
 * here is what stops the UI from offering a delete that appears to do
 * nothing. `key_source` is the server field this reads.
 */
private fun keyStateText(
    configured: Boolean,
    keyless: Boolean,
    keyMasked: String,
    keySource: String,
): String = when {
    keyless -> "No API key needed"
    !configured -> "No API key set"
    keySource == "environment" ->
        "Key from the server's environment${maskSuffix(keyMasked)} - set where Aura is deployed"
    keySource == "store" -> "Key saved on the server${maskSuffix(keyMasked)}"
    else -> "Key configured${maskSuffix(keyMasked)}"
}

private fun maskSuffix(keyMasked: String): String =
    if (keyMasked.isBlank()) "" else " ($keyMasked)"

/** A model, and whether it is the one in use. */
@Composable
fun ModelCard(
    name: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    note: String? = null,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(14.dp),
        color = if (selected) {
            MaterialTheme.colorScheme.primary.copy(alpha = 0.14f)
        } else {
            MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f)
        },
        onClick = onClick,
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp),
        ) {
            Column(modifier = Modifier.weight(1f)) {

                Text(
                    text = name,
                    style = MaterialTheme.typography.bodyLarge,
                    fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                )

                if (!note.isNullOrBlank()) {
                    Spacer(Modifier.height(2.dp))
                    Text(
                        text = note,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            if (selected) {
                Icon(
                    imageVector = Icons.Filled.Check,
                    contentDescription = "In use",
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(20.dp),
                )
            }
        }
    }
}

// ----------------------------------------------------------------------
// API keys
// ----------------------------------------------------------------------

/**
 * Enter or replace one provider's API key.
 *
 * The field starts empty even when a key is stored, and the stored key's
 * mask sits above it as a label. That is the whole design: there is no
 * state in which the widget holds something that *looks* like the current
 * key, so there is no way to accidentally re-submit a mask, and no way for
 * a screenshot or a recomposition dump to contain a secret.
 *
 * `PasswordVisualTransformation` is unconditional - there is no reveal
 * toggle. A key is pasted, not typed, so the usual argument for one does
 * not apply, and its absence removes a state where the key is on screen in
 * clear text.
 */
@Composable
fun ApiKeyField(
    providerLabel: String,
    onSave: (String) -> Unit,
    modifier: Modifier = Modifier,
    keyMasked: String = "",
    keySource: String = "",
    saving: Boolean = false,
    error: String? = null,
    onDelete: (() -> Unit)? = null,
) {
    var entry by remember(providerLabel) { mutableStateOf("") }

    var confirmingDelete by remember(providerLabel) { mutableStateOf(false) }

    val fromEnvironment = keySource == "environment"

    Column(modifier = modifier.fillMaxWidth()) {

        Row(verticalAlignment = Alignment.CenterVertically) {

            Column(modifier = Modifier.weight(1f)) {

                Text(
                    text = providerLabel,
                    style = MaterialTheme.typography.titleMedium,
                )

                if (keyMasked.isNotBlank()) {
                    Spacer(Modifier.height(2.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            imageVector = Icons.Filled.Lock,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(13.dp),
                        )
                        Spacer(Modifier.width(5.dp))
                        Text(
                            text = keyMasked,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }

            if (onDelete != null && keyMasked.isNotBlank() && !fromEnvironment) {
                IconButton(onClick = { confirmingDelete = true }) {
                    Icon(
                        imageVector = Icons.Filled.Delete,
                        contentDescription = "Delete $providerLabel key",
                        tint = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }

        if (fromEnvironment) {
            Spacer(Modifier.height(8.dp))
            NoticeCard(
                text = "This key comes from the server's own environment. " +
                    "Saving here overrides it for this deployment; it cannot be " +
                    "deleted from the app.",
                tone = StatusTone.Neutral,
            )
        }

        Spacer(Modifier.height(10.dp))

        OutlinedTextField(
            value = entry,
            onValueChange = { entry = it },
            modifier = Modifier.fillMaxWidth(),
            label = {
                Text(if (keyMasked.isBlank()) "API key" else "Replace key")
            },
            placeholder = { Text("Paste the key") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(
                keyboardType = KeyboardType.Password,
                imeAction = ImeAction.Done,
                autoCorrectEnabled = false,
            ),
            isError = error != null,
            supportingText = if (error != null) {
                { Text(error, color = MaterialTheme.colorScheme.error) }
            } else {
                null
            },
            enabled = !saving,
            shape = RoundedCornerShape(14.dp),
        )

        Spacer(Modifier.height(10.dp))

        Button(
            onClick = {
                onSave(entry.trim())
                // Cleared here rather than on the reported result: the key
                // has left for the server either way, and keeping it in
                // composition state while a request is in flight is the one
                // window where it could end up in a saved-state bundle.
                entry = ""
            },
            enabled = !saving && entry.isNotBlank(),
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (saving) {
                CircularProgressIndicator(
                    modifier = Modifier.size(16.dp),
                    strokeWidth = 2.dp,
                )
                Spacer(Modifier.width(10.dp))
            }
            Text("Save key")
        }
    }

    if (confirmingDelete && onDelete != null) {
        AlertDialog(
            onDismissRequest = { confirmingDelete = false },
            title = { Text("Delete $providerLabel key?") },
            text = {
                Text(
                    "Aura will stop using $providerLabel until a new key is " +
                        "saved. If it is the primary provider, replies will " +
                        "come from a fallback instead."
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirmingDelete = false
                        onDelete()
                    }
                ) {
                    Text("Delete", color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirmingDelete = false }) { Text("Cancel") }
            },
        )
    }
}

// ----------------------------------------------------------------------
// Destructive actions
// ----------------------------------------------------------------------

/**
 * An action that cannot be undone, behind a confirmation.
 *
 * STEP 9 requires the confirmation and this is the only component that
 * provides one, so a destructive action added later has to either use this
 * or visibly not use it.
 */
@Composable
fun DangerActionCard(
    title: String,
    description: String,
    actionLabel: String,
    confirmBody: String,
    onConfirm: () -> Unit,
    modifier: Modifier = Modifier,
    busy: Boolean = false,
    enabled: Boolean = true,
) {
    var confirming by remember { mutableStateOf(false) }

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        color = MaterialTheme.colorScheme.error.copy(alpha = 0.08f),
    ) {
        Column(modifier = Modifier.padding(18.dp)) {

            Text(
                text = title,
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.error,
            )

            Spacer(Modifier.height(4.dp))

            Text(
                text = description,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Spacer(Modifier.height(14.dp))

            OutlinedButton(
                onClick = { confirming = true },
                enabled = enabled && !busy,
            ) {
                if (busy) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(14.dp),
                        strokeWidth = 2.dp,
                    )
                    Spacer(Modifier.width(8.dp))
                }
                Text(actionLabel, color = MaterialTheme.colorScheme.error)
            }
        }
    }

    if (confirming) {
        AlertDialog(
            onDismissRequest = { confirming = false },
            title = { Text(title) },
            text = { Text(confirmBody) },
            confirmButton = {
                TextButton(
                    onClick = {
                        confirming = false
                        onConfirm()
                    }
                ) {
                    Text(actionLabel, color = MaterialTheme.colorScheme.error)
                }
            },
            dismissButton = {
                TextButton(onClick = { confirming = false }) { Text("Cancel") }
            },
        )
    }
}
