package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Bolt
import androidx.compose.material.icons.filled.Cloud
import androidx.compose.material.icons.filled.Key
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aura.companion.data.remote.ProviderDto
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.ApiKeyField
import com.aura.companion.ui.components.ChoiceDialog
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.ProviderCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SectionHeader
import com.aura.companion.ui.components.SelectRow
import com.aura.companion.ui.components.SettingsCard
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.SliderRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.TextEntryDialog
import com.aura.companion.ui.components.ToggleRow
import com.aura.companion.ui.components.providerFacts
import kotlin.math.roundToInt

/**
 * Settings → AI & Models.
 *
 * The screen a dead provider is fixed from. Everything a Render deployment
 * would otherwise need a file edit and a redeploy for - a new key, a
 * different primary, a reordered fallback chain - happens here, over the
 * authenticated API.
 *
 * MODELS ARE NOT INVENTED
 * -----------------------
 * The model list per provider comes from `PROVIDER_CAPABILITIES` on the
 * server, which lists what that provider class is known to work with. For
 * anything else there is a free-text entry that the server validates -
 * rather than a list of model names this app guessed at, which would go
 * stale the week after it shipped.
 */
@Composable
fun ModelsSection(
    state: HubUiState,
    viewModel: HubViewModel,
    onBack: () -> Unit,
) {
    var picking by remember { mutableStateOf<Picker?>(null) }

    var keyFor by remember { mutableStateOf<String?>(null) }

    var editingLane by remember { mutableStateOf<TaskLane?>(null) }

    var editingCustomEndpoint by remember { mutableStateOf(false) }

    var editingCustomModel by remember { mutableStateOf(false) }

    val server = state.server

    val llm = server.config.llm

    // The primary provider, and where its model is stored - both derived
    // once on the state so this screen and the dialogs below cannot
    // disagree about which `llm.*_model` a choice belongs in.
    val primary = state.primaryProvider

    val modelSetting = state.modelSetting

    HubSection(
        title = "AI & Models",
        subtitle = "Provider, model and API keys",
        onBack = onBack,
        onRefresh = viewModel::refresh,
    ) {

        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        if (!server.keysPersistent) {
            NoticeCard(
                text = server.keyStorageNote.ifBlank {
                    "This server has no encryption secret, so API keys saved " +
                        "here will work now but will be lost when it restarts."
                },
                tone = StatusTone.Warning,
            )
            Spacer(Modifier.height(4.dp))
        }

        // ------------------------------------------------------------------
        // Active configuration
        // ------------------------------------------------------------------

        SettingsSection(title = "Active") {

            SelectRow(
                title = "Provider",
                value = primary?.label ?: llm.provider.ifBlank { "—" },
                icon = Icons.Filled.Bolt,
                lockedReason = state.lockedReason("llm.provider"),
                onClick = { picking = Picker.Provider },
            )

            RowDivider()

            SelectRow(
                title = "Model",
                value = state.activeModel.ifBlank { "Provider default" },
                subtitle = primary?.let { "for ${it.label}" },
                icon = Icons.Filled.Cloud,
                lockedReason = state.lockedReason(modelSetting),
                onClick = { picking = Picker.Model },
            )

            RowDivider()

            SelectRow(
                title = "Fallback chain",
                value = llm.fallbackProviders
                    .takeIf { it.isNotEmpty() }
                    ?.joinToString(" → ")
                    ?: "None",
                subtitle = "Tried in order when the primary fails",
                icon = Icons.Filled.Tune,
                lockedReason = state.lockedReason("llm.fallback_providers"),
                onClick = { picking = Picker.Fallback },
            )
        }

        // ------------------------------------------------------------------
        // Generation
        // ------------------------------------------------------------------

        SettingsSection(
            title = "Generation",
            subtitle = "How Aura writes its replies",
        ) {

            SliderRow(
                title = "Temperature",
                value = llm.temperature.toFloat(),
                range = 0f..2f,
                steps = 19,
                subtitle = "Lower is more predictable",
                lockedReason = state.lockedReason("llm.temperature"),
                onCommit = { viewModel.setNumber("llm.temperature", round2(it)) },
                format = { "%.1f".format(it) },
            )

            RowDivider()

            SliderRow(
                title = "Reply length",
                value = llm.maxOutputTokens.toFloat(),
                range = 64f..4096f,
                subtitle = "Maximum tokens in one reply",
                lockedReason = state.lockedReason("llm.max_output_tokens"),
                onCommit = {
                    viewModel.setNumber("llm.max_output_tokens", it.roundToInt())
                },
                format = { "${it.roundToInt()} tokens" },
            )

            RowDivider()

            SliderRow(
                title = "Timeout",
                value = llm.timeout.toFloat(),
                range = 5f..300f,
                subtitle = "How long to wait for a provider",
                lockedReason = state.lockedReason("llm.timeout"),
                onCommit = { viewModel.setNumber("llm.timeout", it.roundToInt()) },
                format = { "${it.roundToInt()}s" },
            )
        }

        // ------------------------------------------------------------------
        // Task routing and the custom endpoint (phase 23)
        //
        // The five lanes and the two custom-provider keys have been
        // settable over PATCH since the capability router shipped, with no
        // control anywhere. A lane left blank is not a missing value - it
        // is "use the primary", which is what every install had before
        // lanes existed, so blank is offered as a choice rather than
        // hidden.
        // ------------------------------------------------------------------

        SettingsSection(
            title = "Task routing",
            subtitle = "Which provider handles which kind of question",
        ) {

            TASK_LANES.forEach { lane ->

                SelectRow(
                    title = lane.label,
                    value = lane.read(llm.taskModels).ifBlank {
                        "Same as primary"
                    },
                    subtitle = lane.subtitle,
                    icon = Icons.Filled.Tune,
                    lockedReason = state.lockedReason(lane.path),
                    onClick = { editingLane = lane },
                )

                RowDivider()
            }
        }

        SettingsSection(
            title = "Custom endpoint",
            subtitle = "For an OpenAI-compatible gateway",
        ) {

            SelectRow(
                title = "Endpoint URL",
                value = llm.customBaseUrl.ifBlank { "Not set" },
                subtitle = "Must be a complete http(s) address. Needs a restart.",
                icon = Icons.Filled.Link,
                lockedReason = state.lockedReason("llm.custom_base_url"),
                onClick = { editingCustomEndpoint = true },
            )

            RowDivider()

            SelectRow(
                title = "Model",
                value = llm.customModel.ifBlank { "Not set" },
                subtitle = "The model name your gateway expects. Needs a restart.",
                icon = Icons.Filled.Cloud,
                lockedReason = state.lockedReason("llm.custom_model"),
                onClick = { editingCustomModel = true },
            )
        }

        // ------------------------------------------------------------------
        // Providers
        // ------------------------------------------------------------------

        SectionHeader(
            title = "Providers",
            subtitle = "What each one supports in this build of Aura",
        )

        if (server.providers.isEmpty()) {
            SettingsCard {
                Text(
                    // An empty list, a failed request and an unreachable
                    // server are three different sentences. Saying "no
                    // providers reported" for a request that came back 500
                    // reports the server's silence as its answer.
                    text = server.providersError?.userMessage
                        ?: if (server.loaded) {
                            "No providers reported."
                        } else {
                            "Connect to Aura to manage providers."
                        },
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(20.dp),
                )
            }
        }

        Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {

            server.providers.forEach { provider ->

                val outcome = state.providerAction.results[provider.name]

                ProviderCard(
                    label = provider.label.ifBlank { provider.name },
                    capabilities = provider.capabilityLabels(),
                    facts = providerFacts(
                        provider,
                        server.health.providers[provider.name],
                    ),
                    configured = provider.configured,
                    keyless = provider.keyless,
                    keyMasked = provider.keyMasked,
                    keySource = provider.keySource,
                    isPrimary = provider.name == llm.provider,
                    isFallback = provider.name in llm.fallbackProviders,
                    testing = state.providerAction.testing == provider.name,
                    testResult = outcome?.message,
                    testOk = outcome?.ok == true,
                    onTest = { viewModel.testProvider(provider.name) },
                    onSetPrimary = { viewModel.setPrimaryProvider(provider.name) },
                    onToggleFallback = { viewModel.toggleFallback(provider.name) },
                    onManageKey = { keyFor = provider.name },
                )
            }
        }

        Spacer(Modifier.height(32.dp))
    }

    // ------------------------------------------------------------------
    // Dialogs
    // ------------------------------------------------------------------

    when (picking) {

        Picker.Provider -> ChoiceDialog(
            title = "Primary provider",
            options = server.providers.filter { it.chat }.map { it.name },
            selected = llm.provider,
            labels = { name -> server.providers.labelFor(name) },
            onPick = viewModel::setPrimaryProvider,
            onDismiss = { picking = null },
            emptyMessage = "Connect to Aura to see its providers.",
        )

        Picker.Model -> {

            val known = state.modelChoices

            if (known.isEmpty()) {
                TextEntryDialog(
                    title = "Model",
                    initial = state.activeModel,
                    label = "Model name",
                    help = "Aura does not carry a model list for " +
                        "${primary?.label ?: "this provider"}. Enter the model " +
                        "name exactly as the provider spells it.",
                    onCommit = { viewModel.setModel(it, modelSetting) },
                    onDismiss = { picking = null },
                )
            } else {
                ChoiceDialog(
                    title = "Model",
                    options = known,
                    selected = state.activeModel,
                    onPick = { viewModel.setModel(it, modelSetting) },
                    onDismiss = { picking = null },
                )
            }
        }

        Picker.Fallback -> FallbackDialog(
            providers = server.providers.filter {
                it.chat && it.name != llm.provider
            },
            selected = llm.fallbackProviders,
            onToggle = viewModel::toggleFallback,
            onDismiss = { picking = null },
        )

        null -> Unit
    }

    editingLane?.let { lane ->

        TextEntryDialog(
            title = lane.label,
            initial = lane.read(llm.taskModels),
            label = "Provider name",
            help = "The provider that answers ${lane.label.lowercase()} questions" +
                " - for example groq or gemini. Left empty, the primary answers.",
            onCommit = { viewModel.setText(lane.path, it.trim()) },
            onDismiss = { editingLane = null },
        )
    }

    if (editingCustomEndpoint) {
        TextEntryDialog(
            title = "Endpoint URL",
            initial = llm.customBaseUrl,
            label = "https://...",
            help = "The complete base address of an OpenAI-compatible gateway." +
                " The server refuses anything without an explicit scheme - no" +
                " guessing http versus https, so a mistyped URL cannot send" +
                " the key in cleartext. Cleared rather than set on an empty" +
                " commit.",
            onCommit = { viewModel.setText("llm.custom_base_url", it.trim()) },
            onDismiss = { editingCustomEndpoint = false },
        )
    }

    if (editingCustomModel) {
        TextEntryDialog(
            title = "Custom model",
            initial = llm.customModel,
            label = "Model name",
            help = "The model name your gateway expects. Unlike every vendor" +
                " model this one can be cleared, which sends nothing and lets" +
                " the gateway decide.",
            onCommit = { viewModel.setText("llm.custom_model", it.trim()) },
            onDismiss = { editingCustomModel = false },
        )
    }

    keyFor?.let { name ->

        val provider = server.providers.firstOrNull { it.name == name }

        ApiKeyDialog(
            provider = provider,
            saving = state.providerAction.savingKey == name,
            error = state.providerAction.keyError,
            onSave = { key -> viewModel.saveProviderKey(name, key) },
            onDelete = { viewModel.deleteProviderKey(name) },
            onDismiss = { keyFor = null },
        )
    }
}

private enum class Picker { Provider, Model, Fallback }

/**
 * One routing lane, as data.
 *
 * [read] is a function rather than a mirrored copy of [TaskModelsDto] so
 * adding a lane on the server means adding one entry here and nowhere else;
 * the five paths must match `core/settings_store.py` exactly or the PATCH
 * is refused with 422 - which the row then shows, rather than silently
 * doing nothing.
 */
private data class TaskLane(
    val path: String,
    val label: String,
    val subtitle: String,
    val read: (com.aura.companion.data.remote.TaskModelsDto) -> String,
)

private val TASK_LANES = listOf(
    TaskLane(
        path = "llm.task_models.reasoning",
        label = "Reasoning",
        subtitle = "Hard multi-step thinking",
        read = { it.reasoning },
    ),
    TaskLane(
        path = "llm.task_models.coding",
        label = "Coding",
        subtitle = "Writing and changing code",
        read = { it.coding },
    ),
    TaskLane(
        path = "llm.task_models.tool_planning",
        label = "Tool planning",
        subtitle = "Deciding which tools to call",
        read = { it.toolPlanning },
    ),
    TaskLane(
        path = "llm.task_models.fast_response",
        label = "Fast responses",
        subtitle = "Short, quick turns",
        read = { it.fastResponse },
    ),
    TaskLane(
        path = "llm.task_models.long_context",
        label = "Long context",
        subtitle = "Very large conversations or documents",
        read = { it.longContext },
    ),
)

/**
 * The fallback chain, as a set of toggles.
 *
 * Not a [ChoiceDialog] because this is a multi-select and the order is
 * meaningful; each tap sends one PATCH, so the chain the server holds and
 * the chain shown here cannot drift apart.
 */
@Composable
private fun FallbackDialog(
    providers: List<ProviderDto>,
    selected: List<String>,
    onToggle: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Fallback providers") },
        text = {
            Column {

                Text(
                    text = "Tried in order when the primary provider fails. " +
                        "A provider with no key cannot answer, so it is worth " +
                        "testing one before relying on it.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                Spacer(Modifier.height(12.dp))

                providers.forEach { provider ->
                ToggleRow(
                        title = provider.label.ifBlank { provider.name },
                        subtitle = if (provider.configured) {
                            null
                        } else {
                            "No API key set"
                        },
                        checked = provider.name in selected,
                        enabled = provider.configured,
                        onCheckedChange = { onToggle(provider.name) },
                        modifier = Modifier.fillMaxWidth(),
                    )
                }

                if (providers.isEmpty()) {
                    Text(
                        text = "No other providers available.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) {
                Text("Done")
            }
        },
    )
}

/** The API key sheet for one provider. */
@Composable
private fun ApiKeyDialog(
    provider: ProviderDto?,
    saving: Boolean,
    error: String?,
    onSave: (String) -> Unit,
    onDelete: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        icon = {
            Icon(
                Icons.Filled.Key,
                contentDescription = null,
            )
        },
        title = { Text("API key") },
        text = {
            Column {

                Text(
                    text = "Sent once, over the same encrypted connection as " +
                        "everything else, and stored encrypted on the Aura " +
                        "server. It is never sent back to this app.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )

                Spacer(Modifier.height(16.dp))

                ApiKeyField(
                    providerLabel = provider?.label ?: provider?.name.orEmpty(),
                    keyMasked = provider?.keyMasked.orEmpty(),
                    keySource = provider?.keySource.orEmpty(),
                    saving = saving,
                    error = error,
                    onSave = onSave,
                    onDelete = onDelete,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = onDismiss) {
                Text("Close")
            }
        },
    )
}

/**
 * Capability chips, from what the server said its implementation does.
 *
 * A provider with no streaming gets no streaming chip, even where the
 * vendor's API supports it - the badge describes Aura, not the vendor.
 */
private fun ProviderDto.capabilityLabels(): List<String> = buildList {
    if (chat) add("Chat")
    if (streaming) add("Streaming")
    if (tools) add("Tools")
    if (vision) add("Vision")
    if (keyless) add("No key needed")
}

private fun List<ProviderDto>.labelFor(name: String): String =
    firstOrNull { it.name == name }?.label?.takeIf { it.isNotBlank() } ?: name

/** Two decimals, so a slider does not PATCH 0.7000000000000001. */
private fun round2(value: Float): Double =
    (value * 100).roundToInt() / 100.0
