package com.aura.companion.ui.settings

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.aura.companion.R

/**
 * Where the server address and the access token are entered.
 *
 * Neither value ships in the APK - §5 and §6 - so this screen is the only
 * way either one gets into the app. It is also where screen observation is
 * turned on, and the only place that can turn it on.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    viewModel: SettingsViewModel,
    onBack: () -> Unit,
    onRequestScreenObservation: () -> Unit,
    onRequestNotificationPermission: () -> Unit,
) {

    val state by viewModel.state.collectAsStateWithLifecycle()

    var tokenVisible by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.settings_title)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = stringResource(R.string.action_back),
                        )
                    }
                },
            )
        },
    ) { padding ->

        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .imePadding()
                .padding(horizontal = 20.dp, vertical = 12.dp),
        ) {

            SectionHeader(stringResource(R.string.settings_connection))

            OutlinedTextField(
                value = state.serverUrl,
                onValueChange = viewModel::onUrlChanged,
                label = { Text(stringResource(R.string.settings_url_label)) },
                placeholder = { Text("https://aura.example.com") },
                supportingText = { Text(stringResource(R.string.settings_url_help)) },
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
                label = { Text(stringResource(R.string.settings_token_label)) },
                supportingText = { Text(stringResource(R.string.settings_token_help)) },
                singleLine = true,
                visualTransformation = if (tokenVisible) {
                    VisualTransformation.None
                } else {
                    PasswordVisualTransformation()
                },
                trailingIcon = {
                    androidx.compose.material3.TextButton(
                        onClick = { tokenVisible = !tokenVisible }
                    ) {
                        Text(
                            stringResource(
                                if (tokenVisible) R.string.action_hide else R.string.action_show
                            )
                        )
                    }
                },
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Password,
                    imeAction = ImeAction.Done,
                ),
                modifier = Modifier.fillMaxWidth(),
            )

            if (state.insecureRemote) {
                Spacer(Modifier.height(8.dp))
                Warning(stringResource(R.string.settings_insecure_warning))
            }

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
                    Text(stringResource(R.string.settings_test))
                }

                Spacer(Modifier.size(12.dp))

                OutlinedButton(
                    onClick = viewModel::save,
                    enabled = state.serverUrl.isNotBlank(),
                ) {
                    Text(stringResource(R.string.settings_save))
                }
            }

            state.testResult?.let { result ->
                Spacer(Modifier.height(12.dp))
                when (result) {
                    is TestResult.Success -> Notice(
                        text = stringResource(
                            R.string.settings_test_ok,
                            result.provider,
                            result.version,
                        ),
                        colour = MaterialTheme.colorScheme.primary,
                    )

                    is TestResult.Failure -> Notice(
                        text = result.message,
                        colour = MaterialTheme.colorScheme.error,
                    )
                }
            }

            Spacer(Modifier.height(20.dp))
            HorizontalDivider()
            Spacer(Modifier.height(12.dp))

            SectionHeader(stringResource(R.string.settings_awareness))

            ToggleRow(
                title = stringResource(R.string.settings_screen_title),
                subtitle = stringResource(R.string.settings_screen_help),
                checked = state.screenObservation,
                onCheckedChange = { enabled ->
                    viewModel.onScreenObservationChanged(enabled)
                    if (enabled) onRequestScreenObservation()
                },
            )

            ToggleRow(
                title = stringResource(R.string.settings_upload_title),
                subtitle = stringResource(R.string.settings_upload_help),
                checked = state.uploadScreenshots,
                enabled = state.screenObservation,
                onCheckedChange = viewModel::onUploadScreenshotsChanged,
            )

            ToggleRow(
                title = stringResource(R.string.settings_notifications_title),
                subtitle = stringResource(R.string.settings_notifications_help),
                checked = state.notifications,
                onCheckedChange = { enabled ->
                    viewModel.onNotificationsChanged(enabled)
                    if (enabled) onRequestNotificationPermission()
                },
            )

            Spacer(Modifier.height(20.dp))
            HorizontalDivider()
            Spacer(Modifier.height(12.dp))

            SectionHeader(stringResource(R.string.settings_device))

            Text(
                text = stringResource(R.string.settings_device_id, state.deviceId),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            Spacer(Modifier.height(16.dp))

            OutlinedButton(onClick = viewModel::disconnect) {
                Text(stringResource(R.string.settings_disconnect))
            }

            Spacer(Modifier.height(32.dp))
        }
    }
}

@Composable
private fun SectionHeader(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.titleMedium,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(bottom = 12.dp),
    )
}

@Composable
private fun ToggleRow(
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit,
    enabled: Boolean = true,
) {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 8.dp),
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = title,
                style = MaterialTheme.typography.bodyLarge,
                fontWeight = FontWeight.Medium,
            )
            Text(
                text = subtitle,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        Spacer(Modifier.size(12.dp))

        Switch(
            checked = checked,
            onCheckedChange = onCheckedChange,
            enabled = enabled,
        )
    }
}

@Composable
private fun Notice(text: String, colour: androidx.compose.ui.graphics.Color) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodyMedium,
        color = colour,
    )
}

@Composable
private fun Warning(text: String) {
    Surface(
        color = MaterialTheme.colorScheme.errorContainer,
        shape = MaterialTheme.shapes.small,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Text(
            text = text,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onErrorContainer,
            modifier = Modifier.padding(12.dp),
        )
    }
}
