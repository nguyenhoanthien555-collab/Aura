package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.aura.companion.ui.components.DangerActionCard
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SectionHeader
import com.aura.companion.ui.components.SettingsCard
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.StatusRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.settings.SettingsViewModel
import com.aura.companion.ui.settings.TestResult

/**
 * Settings → Connection.
 *
 * REUSES THE EXISTING VIEWMODEL
 * -----------------------------
 * [SettingsViewModel] already owns the URL, the token, save, test and
 * disconnect - including the normalisation that turns `192.168.1.10:8000`
 * into something OkHttp accepts. This screen is a new surface on it, not a
 * second connection store: two places that can write the server address
 * would eventually disagree about which one is live.
 *
 * WHAT IS NOT SHOWN
 * -----------------
 * The token is masked by default and revealed only on an explicit tap, and
 * it is never rendered anywhere else in the hub - not in Advanced, not in
 * diagnostics. It is a bearer credential: anything that prints it prints
 * full access to the deployment.
 */
@Composable
fun ConnectionSection(
    hub: HubUiState,
    viewModel: SettingsViewModel,
    onBack: () -> Unit,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    var tokenVisible by remember { mutableStateOf(false) }

    HubSection(
        title = "Connection",
        subtitle = "Where Aura lives and how this phone proves itself",
        onBack = onBack,
    ) {

        SettingsSection(title = "Status") {

            StatusRow(
                title = "Aura",
                value = when {
                    hub.connected -> "Connected"
                    state.serverUrl.isBlank() -> "Not set up"
                    else -> "Unreachable"
                },
                subtitle = state.serverUrl.ifBlank { "No server address yet" },
                icon = Icons.Filled.Dns,
                tone = when {
                    hub.connected -> StatusTone.Good
                    state.serverUrl.isBlank() -> StatusTone.Neutral
                    else -> StatusTone.Bad
                },
            )

            RowDivider()

            StatusRow(
                title = "Transport",
                value = if (state.serverUrl.startsWith("https://")) "HTTPS" else "HTTP",
                subtitle = if (state.serverUrl.startsWith("https://")) {
                    "Encrypted in transit"
                } else {
                    "Not encrypted - fine on your own network"
                },
                icon = Icons.Filled.Lock,
                tone = when {
                    state.serverUrl.isBlank() -> StatusTone.Neutral
                    state.serverUrl.startsWith("https://") -> StatusTone.Good
                    else -> StatusTone.Warning
                },
            )
        }

        SectionHeader(
            title = "Server",
            subtitle = "Saved encrypted on this phone",
        )

        SettingsCard {

            Column(modifier = Modifier.padding(20.dp)) {

                OutlinedTextField(
                    value = state.serverUrl,
                    onValueChange = viewModel::onUrlChanged,
                    label = { Text("Server address") },
                    placeholder = { Text("https://aura.example.com") },
                    supportingText = {
                        Text("The address your Aura is reachable at")
                    },
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Uri,
                        imeAction = ImeAction.Next,
                    ),
                    modifier = Modifier.fillMaxWidth(),
                )

                Spacer(Modifier.height(12.dp))

                OutlinedTextField(
                    value = state.authToken,
                    onValueChange = viewModel::onTokenChanged,
                    label = { Text("Access token") },
                    supportingText = {
                        Text("Set on the server. Without it, Aura will not answer.")
                    },
                    singleLine = true,
                    // Masked until asked for. The token is the credential
                    // that authorises every settings and API-key change.
                    visualTransformation = if (tokenVisible) {
                        VisualTransformation.None
                    } else {
                        PasswordVisualTransformation()
                    },
                    trailingIcon = {
                        TextButton(onClick = { tokenVisible = !tokenVisible }) {
                            Text(if (tokenVisible) "Hide" else "Show")
                        }
                    },
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Password,
                        imeAction = ImeAction.Done,
                    ),
                    modifier = Modifier.fillMaxWidth(),
                )

                Spacer(Modifier.height(16.dp))

                Row {

                    Button(
                        onClick = viewModel::testConnection,
                        enabled = state.serverUrl.isNotBlank() && !state.isTesting,
                    ) {
                        if (state.isTesting) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                strokeWidth = 2.dp,
                                color = MaterialTheme.colorScheme.onPrimary,
                            )
                            Spacer(Modifier.size(8.dp))
                        }
                        Text("Test")
                    }

                    Spacer(Modifier.size(12.dp))

                    OutlinedButton(
                        onClick = viewModel::save,
                        enabled = state.serverUrl.isNotBlank(),
                    ) {
                        Text("Save")
                    }
                }
            }
        }

        state.testResult?.let { result ->
            Spacer(Modifier.height(12.dp))
            when (result) {
                is TestResult.Success -> NoticeCard(
                    text = "Aura ${result.version} answered, running " +
                        "${result.provider}.",
                    tone = StatusTone.Good,
                )

                is TestResult.Failure -> NoticeCard(
                    text = result.message,
                    tone = StatusTone.Bad,
                )
            }
        }

        if (state.insecureRemote) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = "This address is plain HTTP to a public host, so your " +
                    "token and your conversations travel unencrypted. Use " +
                    "HTTPS for anything outside your own network.",
                tone = StatusTone.Warning,
            )
        }

        SectionHeader(title = "Disconnect")

        DangerActionCard(
            title = "Forget this server",
            description = "Erase the address and token from this phone and stop " +
                "observing the screen. Nothing on the server is deleted.",
            actionLabel = "Disconnect",
            confirmBody = "The server address and access token are removed from " +
                "this phone, and screen observation is turned off. You will " +
                "need the token again to reconnect.",
            enabled = state.serverUrl.isNotBlank(),
            onConfirm = viewModel::disconnect,
        )

        Spacer(Modifier.height(12.dp))

        NoticeCard(
            text = "Both values are stored in Android's encrypted preferences, " +
                "backed by the device keystore. Neither is in the app itself.",
            tone = StatusTone.Neutral,
        )

        Spacer(Modifier.height(32.dp))
    }
}
