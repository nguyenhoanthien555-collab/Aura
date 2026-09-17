package com.aura.companion.ui.hub

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudDone
import androidx.compose.material.icons.filled.CloudSync
import androidx.compose.material.icons.filled.ErrorOutline
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.Inbox
import androidx.compose.material.icons.filled.Outbox
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.StatusRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.ToggleRow
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Settings -> Distributed Sync.
 *
 * Provides real-time visibility into the peer-to-peer event synchronization engine:
 * - Distributed event sync enable/disable
 * - Local & remote ledger cursors
 * - Outbox pending queue depth
 * - Inbox processed events
 * - Manual on-demand sync trigger
 */
@Composable
fun SyncSection(
    state: HubUiState,
    viewModel: HubViewModel,
    onBack: () -> Unit,
) {
    val syncState by viewModel.syncState.collectAsState()

    HubSection(
        title = "Distributed Sync",
        subtitle = "Event streams, cursors & ledger replication",
        onBack = onBack,
        onRefresh = {
            viewModel.refresh()
            viewModel.refreshSyncState()
        },
    ) {
        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        SettingsSection(
            title = "Sync Engine",
            subtitle = "Distributed event replication between phone and Aura server",
        ) {
            ToggleRow(
                title = "Background event sync",
                subtitle = "Periodically sync events, cursors, and outbox with peer",
                icon = Icons.Filled.Sync,
                checked = state.device.syncEnabled,
                onCheckedChange = viewModel::setSyncEnabled,
            )
        }

        SettingsSection(
            title = "Telemetry & Ledgers",
            subtitle = "State of local outbox, inbox, and peer cursor",
        ) {
            StatusRow(
                title = "Local Node ID",
                value = state.device.deviceId.ifBlank { "Unassigned" },
                subtitle = "Cryptographic identity of this Android client",
                icon = Icons.Filled.Hub,
            )

            RowDivider()

            StatusRow(
                title = "Remote Peer",
                value = "RENDER",
                subtitle = "Configured upstream server peer",
                tone = if (state.connected) StatusTone.Good else StatusTone.Warning,
                icon = Icons.Filled.CloudSync,
            )

            RowDivider()

            StatusRow(
                title = "Peer Cursor",
                value = "${syncState.localCursor}",
                subtitle = "Last acknowledged sequence ID from RENDER",
                tone = StatusTone.Neutral,
            )

            RowDivider()

            StatusRow(
                title = "Outbox Pending",
                value = "${syncState.pendingOutboxCount} events",
                subtitle = "Queued client events waiting for upload",
                tone = if (syncState.pendingOutboxCount > 0) StatusTone.Warning else StatusTone.Good,
                icon = Icons.Filled.Outbox,
            )

            RowDivider()

            StatusRow(
                title = "Inbox Processed",
                value = "${syncState.inboxEventCount} events",
                subtitle = "Events successfully received and applied",
                tone = StatusTone.Neutral,
                icon = Icons.Filled.Inbox,
            )

            RowDivider()

            val lastSyncStr = if (syncState.lastSyncTime > 0) {
                SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date(syncState.lastSyncTime))
            } else {
                "Never"
            }

            StatusRow(
                title = "Last Sync Cycle",
                value = lastSyncStr,
                subtitle = syncState.lastSyncResult ?: "Idle",
                tone = if (syncState.lastSyncError != null) StatusTone.Bad else if (syncState.lastSyncTime > 0) StatusTone.Good else StatusTone.Neutral,
                icon = Icons.Filled.CloudDone,
            )
        }

        if (syncState.lastSyncError != null) {
            Spacer(Modifier.height(12.dp))
            NoticeCard(
                text = "Sync failure: ${syncState.lastSyncError}",
                tone = StatusTone.Bad,
                icon = Icons.Filled.ErrorOutline,
            )
        }

        Spacer(Modifier.height(16.dp))

        Surface(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(20.dp),
            color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f),
        ) {
            Column(modifier = Modifier.padding(18.dp)) {
                Text(
                    text = "Manual Synchronization",
                    style = MaterialTheme.typography.titleMedium,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    text = "Trigger an immediate full two-way sync cycle: push all pending outbox events and pull incoming server events.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(14.dp))
                Button(
                    onClick = viewModel::triggerSyncNow,
                    enabled = state.device.syncEnabled && !syncState.isSyncing,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    if (syncState.isSyncing) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(16.dp),
                            strokeWidth = 2.dp,
                            color = MaterialTheme.colorScheme.onPrimary,
                        )
                        Spacer(Modifier.width(8.dp))
                        Text("Synchronizing...")
                    } else {
                        Icon(
                            imageVector = Icons.Filled.Refresh,
                            contentDescription = null,
                            modifier = Modifier.size(18.dp),
                        )
                        Spacer(Modifier.width(8.dp))
                        Text("Sync Now")
                    }
                }
            }
        }

        Spacer(Modifier.height(32.dp))
    }
}
