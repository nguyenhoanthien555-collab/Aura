package com.aura.companion.ui.hub

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AutoStories
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Hub
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Security
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Storage
import androidx.compose.material.icons.filled.Timeline
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Tab
import androidx.compose.material3.TabRow
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.aura.companion.data.remote.MemoryEpisodeDto
import com.aura.companion.data.remote.MemoryFactDto
import com.aura.companion.ui.components.AnimatedNotice
import com.aura.companion.ui.components.DangerActionCard
import com.aura.companion.ui.components.NoticeCard
import com.aura.companion.ui.components.RowDivider
import com.aura.companion.ui.components.SectionHeader
import com.aura.companion.ui.components.SettingsSection
import com.aura.companion.ui.components.SliderRow
import com.aura.companion.ui.components.StatusTone
import com.aura.companion.ui.components.StepperRow
import com.aura.companion.ui.components.ToggleRow
import com.aura.companion.ui.theme.auraGlass
import kotlin.math.roundToInt

/**
 * Settings → Memory: Transparent Deep Entity Knowledge Graph & Memory Management Hub.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MemorySection(
    state: HubUiState,
    viewModel: HubViewModel,
    memoryViewModel: MemoryHubViewModel,
    onBack: () -> Unit,
) {
    val memState by memoryViewModel.state.collectAsStateWithLifecycle()
    val memory = state.server.config.memory

    var showAddFactDialog by remember { mutableStateOf(false) }
    var showAddEntityDialog by remember { mutableStateOf(false) }
    var showAddRelationDialog by remember { mutableStateOf(false) }
    var factToDelete by remember { mutableStateOf<String?>(null) }
    var entityToDelete by remember { mutableStateOf<String?>(null) }
    var episodeToDelete by remember { mutableStateOf<Int?>(null) }
    var showPurgeConfirmDialog by remember { mutableStateOf(false) }

    HubSection(
        title = "Memory & Knowledge",
        subtitle = "Quản lý ký ức thực thể, hồ sơ cá nhân và lịch sử sự kiện",
        onBack = onBack,
        onRefresh = {
            viewModel.refresh()
            memoryViewModel.refresh()
        },
    ) {
        AnimatedNotice(text = state.notice?.text, tone = state.notice.tone())

        memState.message?.let { msg ->
            Spacer(Modifier.height(8.dp))
            NoticeCard(
                text = msg,
                tone = StatusTone.Good,
                icon = Icons.Filled.AutoStories,
            )
        }

        memState.error?.let { err ->
            Spacer(Modifier.height(8.dp))
            NoticeCard(
                text = err,
                tone = StatusTone.Bad,
                icon = Icons.Filled.Security,
            )
        }

        Spacer(Modifier.height(12.dp))

        // Multi-tier Tab Selection Row
        val tabs = listOf(
            "Hồ sơ (${memState.facts.size})" to Icons.Filled.Person,
            "Thực thể (${memState.graph.entities.size})" to Icons.Filled.Hub,
            "Sự kiện (${memState.episodes.size})" to Icons.Filled.Timeline,
            "Cài đặt" to Icons.Filled.Settings,
        )

        TabRow(
            selectedTabIndex = memState.selectedTab,
            containerColor = Color.Transparent,
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(16.dp))
                .auraGlass(shape = RoundedCornerShape(16.dp)),
        ) {
            tabs.forEachIndexed { index, (label, icon) ->
                Tab(
                    selected = memState.selectedTab == index,
                    onClick = { memoryViewModel.setTab(index) },
                    text = {
                        Text(
                            text = label,
                            style = MaterialTheme.typography.labelSmall,
                            fontWeight = if (memState.selectedTab == index) FontWeight.Bold else FontWeight.Normal,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    },
                    icon = {
                        Icon(
                            icon,
                            contentDescription = label,
                            modifier = Modifier.size(18.dp),
                        )
                    },
                )
            }
        }

        Spacer(Modifier.height(16.dp))

        // TAB 0: FACTS & PROFILE
        if (memState.selectedTab == 0) {
            FactsTabContent(
                memState = memState,
                onSearchChange = memoryViewModel::setSearchQuery,
                onCategorySelect = memoryViewModel::setCategory,
                onAddFactClick = { showAddFactDialog = true },
                onDeleteFact = { key -> factToDelete = key },
            )
        }

        // TAB 1: ENTITY KNOWLEDGE GRAPH
        if (memState.selectedTab == 1) {
            EntityGraphTabContent(
                memState = memState,
                onSearchChange = memoryViewModel::setSearchQuery,
                onAddEntityClick = { showAddEntityDialog = true },
                onAddRelationClick = { showAddRelationDialog = true },
                onDeleteEntity = { name -> entityToDelete = name },
                onDeleteRelation = { s, r, t -> memoryViewModel.deleteRelation(s, r, t) },
            )
        }

        // TAB 2: EPISODIC TIMELINE
        if (memState.selectedTab == 2) {
            EpisodicTabContent(
                memState = memState,
                onDeleteEpisode = { id -> episodeToDelete = id },
            )
        }

        // TAB 3: SERVER SETTINGS & DANGER ZONE
        if (memState.selectedTab == 3) {
            SettingsTabContent(
                state = state,
                viewModel = viewModel,
                memory = memory,
                onPurgeAllClick = { showPurgeConfirmDialog = true },
            )
        }

        Spacer(Modifier.height(32.dp))
    }

    // Dialog: Add Fact
    if (showAddFactDialog) {
        AddFactDialog(
            onDismiss = { showAddFactDialog = false },
            onConfirm = { key, value, category ->
                memoryViewModel.upsertFact(key, value, category)
                showAddFactDialog = false
            },
        )
    }

    // Dialog: Add Entity
    if (showAddEntityDialog) {
        AddEntityDialog(
            onDismiss = { showAddEntityDialog = false },
            onConfirm = { name, type, desc ->
                memoryViewModel.createEntity(name, type, desc)
                showAddEntityDialog = false
            },
        )
    }

    // Dialog: Add Relation
    if (showAddRelationDialog) {
        AddRelationDialog(
            entities = memState.graph.entities.map { it.name },
            onDismiss = { showAddRelationDialog = false },
            onConfirm = { source, relation, target ->
                memoryViewModel.createRelation(source, relation, target)
                showAddRelationDialog = false
            },
        )
    }

    // Dialog: Delete Fact Confirmation
    factToDelete?.let { key ->
        AlertDialog(
            onDismissRequest = { factToDelete = null },
            title = { Text("Xác nhận quên thông tin?") },
            text = { Text("Aura sẽ xóa fact '$key' khỏi bộ nhớ vĩnh viễn.") },
            confirmButton = {
                Button(
                    onClick = {
                        memoryViewModel.deleteFact(key)
                        factToDelete = null
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                ) {
                    Text("Xóa")
                }
            },
            dismissButton = {
                TextButton(onClick = { factToDelete = null }) {
                    Text("Hủy")
                }
            },
        )
    }

    // Dialog: Delete Entity Confirmation
    entityToDelete?.let { name ->
        AlertDialog(
            onDismissRequest = { entityToDelete = null },
            title = { Text("Xóa thực thể khỏi Graph?") },
            text = { Text("Thực thể '$name' cùng tất cả các mối quan hệ liên kết sẽ bị xóa vĩnh viễn.") },
            confirmButton = {
                Button(
                    onClick = {
                        memoryViewModel.deleteEntity(name)
                        entityToDelete = null
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                ) {
                    Text("Xóa thực thể")
                }
            },
            dismissButton = {
                TextButton(onClick = { entityToDelete = null }) {
                    Text("Hủy")
                }
            },
        )
    }

    // Dialog: Delete Episode Confirmation
    episodeToDelete?.let { id ->
        AlertDialog(
            onDismissRequest = { episodeToDelete = null },
            title = { Text("Xóa ký ức sự kiện #$id?") },
            text = { Text("Sự kiện này sẽ được gỡ khỏi dòng thời gian hội thoại.") },
            confirmButton = {
                Button(
                    onClick = {
                        memoryViewModel.deleteEpisode(id)
                        episodeToDelete = null
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                ) {
                    Text("Xóa")
                }
            },
            dismissButton = {
                TextButton(onClick = { episodeToDelete = null }) {
                    Text("Hủy")
                }
            },
        )
    }

    // Dialog: Purge All Confirmation
    if (showPurgeConfirmDialog) {
        AlertDialog(
            onDismissRequest = { showPurgeConfirmDialog = false },
            icon = { Icon(Icons.Filled.Security, contentDescription = null, tint = MaterialTheme.colorScheme.error) },
            title = { Text("Tẩy sạch toàn bộ ký ức?") },
            text = {
                Text(
                    "CẢNH BÁO: Toàn bộ thông tin cá nhân (Facts), Mạng thực thể (Entity Graph) và Ký ức hội thoại (Episodic) " +
                        "trên máy chủ Aura sẽ bị xóa sạch hoàn toàn. Hành động này không thể hoàn tác!",
                )
            },
            confirmButton = {
                Button(
                    onClick = {
                        memoryViewModel.purge("all")
                        showPurgeConfirmDialog = false
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error),
                ) {
                    Text("Xóa sạch vĩnh viễn")
                }
            },
            dismissButton = {
                TextButton(onClick = { showPurgeConfirmDialog = false }) {
                    Text("Giữ lại")
                }
            },
        )
    }
}

// ----------------------------------------------------------------------
// Tab 0: Facts & Profile Content
// ----------------------------------------------------------------------
@Composable
private fun FactsTabContent(
    memState: MemoryHubUiState,
    onSearchChange: (String) -> Unit,
    onCategorySelect: (String?) -> Unit,
    onAddFactClick: () -> Unit,
    onDeleteFact: (String) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = "Hồ sơ thực tế (${memState.filteredFacts.size})",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )
        Button(
            onClick = onAddFactClick,
            shape = RoundedCornerShape(12.dp),
        ) {
            Icon(Icons.Filled.Add, contentDescription = null, modifier = Modifier.size(16.dp))
            Spacer(Modifier.width(4.dp))
            Text("Thêm Fact", style = MaterialTheme.typography.labelMedium)
        }
    }

    Spacer(Modifier.height(8.dp))

    // Search Box
    OutlinedTextField(
        value = memState.searchQuery,
        onValueChange = onSearchChange,
        modifier = Modifier.fillMaxWidth(),
        placeholder = { Text("Tìm kiếm sự thật, sở thích...") },
        leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
        trailingIcon = {
            if (memState.searchQuery.isNotEmpty()) {
                IconButton(onClick = { onSearchChange("") }) {
                    Icon(Icons.Filled.Close, contentDescription = "Clear")
                }
            }
        },
        singleLine = true,
        shape = RoundedCornerShape(12.dp),
    )

    Spacer(Modifier.height(8.dp))

    // Category Filter Chips
    val categories = listOf("Tất cả" to null) +
        memState.overview.categories.keys.map { it to it }

    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        categories.forEach { (label, cat) ->
            FilterChip(
                selected = memState.selectedCategory == cat,
                onClick = { onCategorySelect(cat) },
                label = { Text(label) },
            )
        }
    }

    Spacer(Modifier.height(12.dp))

    if (memState.filteredFacts.isEmpty()) {
        SurfaceCard {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Icon(
                    Icons.Filled.Person,
                    contentDescription = null,
                    modifier = Modifier.size(40.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = if (memState.searchQuery.isEmpty()) "Chưa có thông tin hồ sơ nào." else "Không tìm thấy kết quả phù hợp.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    } else {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            memState.filteredFacts.forEach { fact ->
                FactItemCard(fact = fact, onDelete = { onDeleteFact(fact.key) })
            }
        }
    }
}

@Composable
private fun FactItemCard(fact: MemoryFactDto, onDelete: () -> Unit) {
    SurfaceCard {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = fact.key,
                        style = MaterialTheme.typography.labelLarge,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Bold,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    Spacer(Modifier.width(8.dp))
                    Box(
                        modifier = Modifier
                            .background(
                                MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.6f),
                                RoundedCornerShape(6.dp),
                            )
                            .padding(horizontal = 6.dp, vertical = 2.dp),
                    ) {
                        Text(
                            text = fact.category ?: "profile",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onPrimaryContainer,
                        )
                    }
                }
                Spacer(Modifier.height(4.dp))
                Text(
                    text = fact.value,
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
            IconButton(onClick = onDelete) {
                Icon(
                    Icons.Filled.Delete,
                    contentDescription = "Xóa",
                    tint = MaterialTheme.colorScheme.error.copy(alpha = 0.8f),
                    modifier = Modifier.size(20.dp),
                )
            }
        }
    }
}

// ----------------------------------------------------------------------
// Tab 1: Entity Knowledge Graph Content
// ----------------------------------------------------------------------
@Composable
private fun EntityGraphTabContent(
    memState: MemoryHubUiState,
    onSearchChange: (String) -> Unit,
    onAddEntityClick: () -> Unit,
    onAddRelationClick: () -> Unit,
    onDeleteEntity: (String) -> Unit,
    onDeleteRelation: (source: String, relation: String, target: String) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = "Thực thể & Mối quan hệ",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.Bold,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(
                onClick = onAddEntityClick,
                shape = RoundedCornerShape(12.dp),
            ) {
                Text("+ Thực thể", style = MaterialTheme.typography.labelSmall)
            }
            Button(
                onClick = onAddRelationClick,
                shape = RoundedCornerShape(12.dp),
            ) {
                Text("+ Nối quan hệ", style = MaterialTheme.typography.labelSmall)
            }
        }
    }

    Spacer(Modifier.height(8.dp))

    OutlinedTextField(
        value = memState.searchQuery,
        onValueChange = onSearchChange,
        modifier = Modifier.fillMaxWidth(),
        placeholder = { Text("Tìm kiếm thực thể (người, địa điểm, dự án)...") },
        leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
        trailingIcon = {
            if (memState.searchQuery.isNotEmpty()) {
                IconButton(onClick = { onSearchChange("") }) {
                    Icon(Icons.Filled.Close, contentDescription = "Clear")
                }
            }
        },
        singleLine = true,
        shape = RoundedCornerShape(12.dp),
    )

    Spacer(Modifier.height(12.dp))

    if (memState.filteredEntities.isEmpty()) {
        SurfaceCard {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Icon(
                    Icons.Filled.Hub,
                    contentDescription = null,
                    modifier = Modifier.size(40.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "Chưa có thực thể nào trong Knowledge Graph.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    } else {
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            memState.filteredEntities.forEach { entity ->
                val connectedRelations = memState.graph.relations.filter {
                    it.source.equals(entity.name, ignoreCase = true) || it.target.equals(entity.name, ignoreCase = true)
                }

                SurfaceCard {
                    Column(modifier = Modifier.padding(14.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Text(
                                        text = entity.name,
                                        style = MaterialTheme.typography.titleMedium,
                                        fontWeight = FontWeight.Bold,
                                    )
                                    Spacer(Modifier.width(8.dp))
                                    Box(
                                        modifier = Modifier
                                            .background(
                                                MaterialTheme.colorScheme.secondaryContainer.copy(alpha = 0.7f),
                                                RoundedCornerShape(6.dp),
                                            )
                                            .padding(horizontal = 6.dp, vertical = 2.dp),
                                    ) {
                                        Text(
                                            text = entity.entityType,
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onSecondaryContainer,
                                        )
                                    }
                                }
                                if (!entity.description.isNullOrBlank()) {
                                    Spacer(Modifier.height(4.dp))
                                    Text(
                                        text = entity.description,
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                            IconButton(onClick = { onDeleteEntity(entity.name) }) {
                                Icon(
                                    Icons.Filled.Delete,
                                    contentDescription = "Xóa thực thể",
                                    tint = MaterialTheme.colorScheme.error.copy(alpha = 0.8f),
                                    modifier = Modifier.size(18.dp),
                                )
                            }
                        }

                        // Display connected relations
                        if (connectedRelations.isNotEmpty()) {
                            Spacer(Modifier.height(8.dp))
                            RowDivider()
                            Spacer(Modifier.height(8.dp))
                            Text(
                                text = "Mối quan hệ liên kết:",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Spacer(Modifier.height(4.dp))
                            connectedRelations.forEach { rel ->
                                Row(
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .padding(vertical = 2.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Text(
                                        text = rel.source,
                                        style = MaterialTheme.typography.bodySmall,
                                        fontWeight = FontWeight.SemiBold,
                                    )
                                    Spacer(Modifier.width(4.dp))
                                    Icon(
                                        Icons.AutoMirrored.Filled.ArrowForward,
                                        contentDescription = null,
                                        modifier = Modifier.size(12.dp),
                                        tint = MaterialTheme.colorScheme.primary,
                                    )
                                    Spacer(Modifier.width(4.dp))
                                    Box(
                                        modifier = Modifier
                                            .background(
                                                MaterialTheme.colorScheme.tertiaryContainer.copy(alpha = 0.5f),
                                                RoundedCornerShape(4.dp),
                                            )
                                            .padding(horizontal = 4.dp, vertical = 1.dp),
                                    ) {
                                        Text(
                                            text = rel.relation,
                                            style = MaterialTheme.typography.labelSmall,
                                            color = MaterialTheme.colorScheme.onTertiaryContainer,
                                        )
                                    }
                                    Spacer(Modifier.width(4.dp))
                                    Icon(
                                        Icons.AutoMirrored.Filled.ArrowForward,
                                        contentDescription = null,
                                        modifier = Modifier.size(12.dp),
                                        tint = MaterialTheme.colorScheme.primary,
                                    )
                                    Spacer(Modifier.width(4.dp))
                                    Text(
                                        text = rel.target,
                                        style = MaterialTheme.typography.bodySmall,
                                        fontWeight = FontWeight.SemiBold,
                                        modifier = Modifier.weight(1f),
                                    )
                                    IconButton(
                                        onClick = { onDeleteRelation(rel.source, rel.relation, rel.target) },
                                        modifier = Modifier.size(24.dp),
                                    ) {
                                        Icon(
                                            Icons.Filled.Close,
                                            contentDescription = "Hủy quan hệ",
                                            modifier = Modifier.size(14.dp),
                                            tint = MaterialTheme.colorScheme.error,
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

// ----------------------------------------------------------------------
// Tab 2: Episodic Content
// ----------------------------------------------------------------------
@Composable
private fun EpisodicTabContent(
    memState: MemoryHubUiState,
    onDeleteEpisode: (Int) -> Unit,
) {
    Text(
        text = "Dòng thời gian sự kiện (${memState.episodes.size})",
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.Bold,
    )
    Spacer(Modifier.height(8.dp))

    if (memState.episodes.isEmpty()) {
        SurfaceCard {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Icon(
                    Icons.Filled.Timeline,
                    contentDescription = null,
                    modifier = Modifier.size(40.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(8.dp))
                Text(
                    text = "Chưa ghi nhận sự kiện hội thoại nào gần đây.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    } else {
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            memState.episodes.forEach { ep ->
                SurfaceCard {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(14.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                Box(
                                    modifier = Modifier
                                        .background(
                                            MaterialTheme.colorScheme.primaryContainer,
                                            RoundedCornerShape(6.dp),
                                        )
                                        .padding(horizontal = 6.dp, vertical = 2.dp),
                                ) {
                                    Text(
                                        text = ep.category,
                                        style = MaterialTheme.typography.labelSmall,
                                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                                    )
                                }
                                Spacer(Modifier.width(8.dp))
                                Text(
                                    text = "Độ tin cậy: ${(ep.confidence * 100).toInt()}%",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            Spacer(Modifier.height(6.dp))
                            Text(
                                text = ep.content,
                                style = MaterialTheme.typography.bodyMedium,
                            )
                        }
                        IconButton(onClick = { onDeleteEpisode(ep.id) }) {
                            Icon(
                                Icons.Filled.Delete,
                                contentDescription = "Xóa",
                                tint = MaterialTheme.colorScheme.error.copy(alpha = 0.8f),
                                modifier = Modifier.size(20.dp),
                            )
                        }
                    }
                }
            }
        }
    }
}

// ----------------------------------------------------------------------
// Tab 3: Settings & Purge
// ----------------------------------------------------------------------
@Composable
private fun SettingsTabContent(
    state: HubUiState,
    viewModel: HubViewModel,
    memory: com.aura.companion.data.remote.MemoryConfigDto,
    onPurgeAllClick: () -> Unit,
) {
    SettingsSection(title = "Truy xuất & Nhận thức") {
        ToggleRow(
            title = "Sử dụng ký ức khi trả lời",
            subtitle = "Truy vấn các thực thể liên quan và Facts trong các lượt chat",
            icon = Icons.Filled.Search,
            checked = memory.recall,
            pending = "memory.recall" in state.pending,
            lockedReason = state.lockedReason("memory.recall"),
            onCheckedChange = { viewModel.setFlag("memory.recall", it) },
        )
        RowDivider()
        ToggleRow(
            title = "Tự động trích xuất ký ức mới",
            subtitle = "Tiến trình nền phản tư và phân tích thực thể. Cần khởi động lại để đổi.",
            icon = Icons.Filled.AutoStories,
            checked = memory.pipeline,
            pending = "memory.pipeline" in state.pending,
            lockedReason = state.lockedReason("memory.pipeline"),
            onCheckedChange = { viewModel.setFlag("memory.pipeline", it) },
        )
        RowDivider()
        ToggleRow(
            title = "Hồ sơ thực tế (Profile Facts)",
            subtitle = "Lưu giữ chân dung người dùng. Cần khởi động lại để đổi.",
            icon = Icons.Filled.Person,
            checked = memory.profile,
            pending = "memory.profile" in state.pending,
            lockedReason = state.lockedReason("memory.profile"),
            onCheckedChange = { viewModel.setFlag("memory.profile", it) },
        )
        RowDivider()
        ToggleRow(
            title = "Ký ức ngữ nghĩa (Semantic Memory)",
            subtitle = "Chỉ mục vector cho tìm kiếm theo ý nghĩa",
            icon = Icons.Filled.Psychology,
            checked = memory.semantic.enabled,
            pending = "memory.semantic.enabled" in state.pending,
            lockedReason = state.lockedReason("memory.semantic.enabled"),
            onCheckedChange = { viewModel.setFlag("memory.semantic.enabled", it) },
        )
    }

    Spacer(Modifier.height(12.dp))

    SettingsSection(
        title = "Giới hạn ngữ cảnh",
        subtitle = "Lượng thông tin mang vào mỗi lượt chat",
    ) {
        StepperRow(
            title = "Lịch sử hội thoại",
            value = memory.historyLimit,
            range = 1..200,
            subtitle = "Số lượt trao đổi gần nhất được nạp vào context",
            lockedReason = state.lockedReason("memory.history_limit"),
            onCommit = { viewModel.setNumber("memory.history_limit", it) },
            format = { "$it" },
        )
        RowDivider()
        SliderRow(
            title = "Độ sâu tìm kiếm",
            value = memory.retrievalScope.toFloat(),
            range = 10f..5000f,
            subtitle = "Số mục ký ức tối đa được xếp hạng",
            lockedReason = state.lockedReason("memory.retrieval_scope"),
            onCommit = { viewModel.setNumber("memory.retrieval_scope", it.roundToInt()) },
            format = { "${it.roundToInt()}" },
        )
    }

    Spacer(Modifier.height(16.dp))

    SectionHeader(
        title = "Quyền riêng tư & Xóa sạch",
        subtitle = "Tẩy dữ liệu trên máy chủ hoặc khôi phục cấu hình",
    )

    DangerActionCard(
        title = "Tẩy sạch toàn bộ ký ức (Purge All)",
        description = "Xóa vĩnh viễn toàn bộ Profile Facts, Mạng thực thể Knowledge Graph và Dòng thời gian Episodic trên máy chủ.",
        actionLabel = "Tẩy sạch ký ức",
        confirmBody = "Hành động này sẽ xóa sạch toàn bộ ký ức mà Aura đã học hoặc ghi nhớ từ trước tới nay. Không thể phục hồi!",
        enabled = true,
        busy = false,
        onConfirm = onPurgeAllClick,
    )

    Spacer(Modifier.height(12.dp))

    DangerActionCard(
        title = "Khôi phục cài đặt bộ nhớ",
        description = "Đưa các tùy chọn cấu hình bộ nhớ về giá trị mặc định của máy chủ. Ký ức đã lưu không bị xóa.",
        actionLabel = "Khôi phục",
        confirmBody = "Các cờ cấu hình sẽ quay về thiết lập gốc.",
        enabled = state.settingsAvailable,
        busy = state.loading,
        onConfirm = { viewModel.resetSettings(MEMORY_PATHS) },
    )
}

// ----------------------------------------------------------------------
// Dialogs
// ----------------------------------------------------------------------

@Composable
private fun AddFactDialog(onDismiss: () -> Unit, onConfirm: (key: String, value: String, category: String) -> Unit) {
    var key by remember { mutableStateOf("") }
    var value by remember { mutableStateOf("") }
    var category by remember { mutableStateOf("profile") }
    var error by remember { mutableStateOf<String?>(null) }

    val isSensitive = key.contains("pass", ignoreCase = true) ||
        key.contains("matkhau", ignoreCase = true) ||
        key.contains("secret", ignoreCase = true) ||
        key.contains("token", ignoreCase = true) ||
        value.contains("AIzaSy", ignoreCase = false) ||
        value.contains("sk-", ignoreCase = false)

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Thêm thông tin hồ sơ") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = key,
                    onValueChange = { key = it },
                    label = { Text("Tên thuộc tính (Key, vd: favorite_color)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = value,
                    onValueChange = { value = it },
                    label = { Text("Giá trị (Value, vd: Màu xanh lá cây)") },
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = category,
                    onValueChange = { category = it },
                    label = { Text("Danh mục (vd: profile, preferences)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )

                if (isSensitive) {
                    Text(
                        text = "Cảnh báo: Dữ liệu này có vẻ chứa thông tin nhạy cảm/mật khẩu. Máy chủ sẽ chặn lưu trữ theo chính sách bảo mật.",
                        color = MaterialTheme.colorScheme.error,
                        style = MaterialTheme.typography.bodySmall,
                    )
                }

                error?.let {
                    Text(text = it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    if (key.isBlank() || value.isBlank()) {
                        error = "Vui lòng nhập đầy đủ Key và Value"
                    } else {
                        onConfirm(key.trim(), value.trim(), category.trim())
                    }
                },
            ) {
                Text("Lưu Fact")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Hủy") }
        },
    )
}

@Composable
private fun AddEntityDialog(onDismiss: () -> Unit, onConfirm: (name: String, type: String, desc: String) -> Unit) {
    var name by remember { mutableStateOf("") }
    var type by remember { mutableStateOf("CONCEPT") }
    var desc by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Tạo thực thể mới") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Tên thực thể (vd: Aura Project)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = type,
                    onValueChange = { type = it },
                    label = { Text("Loại thực thể (vd: PERSON, PROJECT, TOOL, PLACE)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = desc,
                    onValueChange = { desc = it },
                    label = { Text("Mô tả tóm tắt (tùy chọn)") },
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    if (name.isNotBlank()) onConfirm(name.trim(), type.trim().uppercase(), desc.trim())
                },
            ) {
                Text("Tạo thực thể")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Hủy") }
        },
    )
}

@Composable
private fun AddRelationDialog(
    entities: List<String>,
    onDismiss: () -> Unit,
    onConfirm: (source: String, relation: String, target: String) -> Unit,
) {
    var source by remember { mutableStateOf(entities.firstOrNull() ?: "") }
    var relation by remember { mutableStateOf("") }
    var target by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Nối quan hệ thực thể") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = source,
                    onValueChange = { source = it },
                    label = { Text("Chủ thể (Source, vd: Hoàn Thiện)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = relation,
                    onValueChange = { relation = it },
                    label = { Text("Quan hệ (Relation, vd: PHÁT_TRIỂN, YÊU_THÍCH)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = target,
                    onValueChange = { target = it },
                    label = { Text("Đối tượng (Target, vd: Aura Assistant)") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        },
        confirmButton = {
            Button(
                onClick = {
                    if (source.isNotBlank() && relation.isNotBlank() && target.isNotBlank()) {
                        onConfirm(source.trim(), relation.trim().uppercase(), target.trim())
                    }
                },
            ) {
                Text("Tạo liên kết")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Hủy") }
        },
    )
}

private val MEMORY_PATHS = listOf(
    "memory.recall",
    "memory.profile",
    "memory.pipeline",
    "memory.history_limit",
    "memory.retrieval_scope",
    "memory.semantic.enabled",
)
