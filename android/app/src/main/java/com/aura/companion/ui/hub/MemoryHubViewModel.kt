package com.aura.companion.ui.hub

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.aura.companion.data.AuraRepository
import com.aura.companion.data.AuraResult
import com.aura.companion.data.remote.MemoryEpisodeDto
import com.aura.companion.data.remote.MemoryFactDto
import com.aura.companion.data.remote.MemoryGraphDto
import com.aura.companion.data.remote.MemoryOverviewDto
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class MemoryHubUiState(
    val overview: MemoryOverviewDto = MemoryOverviewDto(),
    val facts: List<MemoryFactDto> = emptyList(),
    val graph: MemoryGraphDto = MemoryGraphDto(),
    val episodes: List<MemoryEpisodeDto> = emptyList(),
    val selectedTab: Int = 0,
    val searchQuery: String = "",
    val selectedCategory: String? = null,
    val isLoading: Boolean = false,
    val message: String? = null,
    val error: String? = null,
) {
    val filteredFacts: List<MemoryFactDto> get() {
        val q = searchQuery.trim().lowercase()
        return facts.filter { fact ->
            val matchCategory = selectedCategory == null || fact.category.equals(selectedCategory, ignoreCase = true)
            val matchQuery = q.isEmpty() || fact.key.lowercase().contains(q) || fact.value.lowercase().contains(q)
            matchCategory && matchQuery
        }
    }

    val filteredEntities: List<com.aura.companion.data.remote.MemoryEntityDto> get() {
        val q = searchQuery.trim().lowercase()
        return if (q.isEmpty()) {
            graph.entities
        } else {
            graph.entities.filter {
                it.name.lowercase().contains(q) ||
                    (it.description?.lowercase()?.contains(q) == true) ||
                    it.entityType.lowercase().contains(q)
            }
        }
    }
}

class MemoryHubViewModel(
    private val repository: AuraRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(MemoryHubUiState())
    val state: StateFlow<MemoryHubUiState> = _state.asStateFlow()

    init {
        refresh()
    }

    fun setTab(index: Int) {
        _state.update { it.copy(selectedTab = index) }
    }

    fun setSearchQuery(q: String) {
        _state.update { it.copy(searchQuery = q) }
    }

    fun setCategory(category: String?) {
        _state.update { it.copy(selectedCategory = category) }
    }

    fun clearMessage() {
        _state.update { it.copy(message = null, error = null) }
    }

    fun refresh() {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }

            // 1. Overview
            when (val res = repository.getMemoryOverview()) {
                is AuraResult.Ok -> _state.update { it.copy(overview = res.value) }
                is AuraResult.Failed -> Unit
            }

            // 2. Facts
            when (val res = repository.getMemoryFacts(limit = 200)) {
                is AuraResult.Ok -> _state.update { it.copy(facts = res.value.facts) }
                is AuraResult.Failed -> Unit
            }

            // 3. Graph
            when (val res = repository.getMemoryGraph(limit = 200)) {
                is AuraResult.Ok -> _state.update { it.copy(graph = res.value) }
                is AuraResult.Failed -> Unit
            }

            // 4. Episodes
            when (val res = repository.getMemoryEpisodes(limit = 50)) {
                is AuraResult.Ok -> _state.update { it.copy(episodes = res.value.episodes) }
                is AuraResult.Failed -> Unit
            }

            _state.update { it.copy(isLoading = false) }
        }
    }

    fun upsertFact(key: String, value: String, category: String = "profile") {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            when (val res = repository.upsertFact(key, value, category)) {
                is AuraResult.Ok -> {
                    _state.update { it.copy(message = "Đã lưu thông tin: $key") }
                    refresh()
                }
                is AuraResult.Failed -> {
                    _state.update { it.copy(isLoading = false, error = "Không thể lưu: ${res.error}") }
                }
            }
        }
    }

    fun deleteFact(key: String) {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            when (val res = repository.deleteFact(key)) {
                is AuraResult.Ok -> {
                    _state.update { it.copy(message = "Đã xóa thông tin: $key") }
                    refresh()
                }
                is AuraResult.Failed -> {
                    _state.update { it.copy(isLoading = false, error = "Lỗi khi xóa: ${res.error}") }
                }
            }
        }
    }

    fun createEntity(name: String, entityType: String = "CONCEPT", description: String = "") {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            when (val res = repository.createEntity(name, entityType, description)) {
                is AuraResult.Ok -> {
                    _state.update { it.copy(message = "Đã tạo thực thể: $name") }
                    refresh()
                }
                is AuraResult.Failed -> {
                    _state.update { it.copy(isLoading = false, error = "Lỗi tạo thực thể: ${res.error}") }
                }
            }
        }
    }

    fun deleteEntity(name: String) {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            when (val res = repository.deleteEntity(name)) {
                is AuraResult.Ok -> {
                    _state.update { it.copy(message = "Đã xóa thực thể: $name") }
                    refresh()
                }
                is AuraResult.Failed -> {
                    _state.update { it.copy(isLoading = false, error = "Lỗi xóa thực thể: ${res.error}") }
                }
            }
        }
    }

    fun createRelation(source: String, relation: String, target: String, confidence: Double = 1.0) {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            when (val res = repository.createRelation(source, relation, target, confidence)) {
                is AuraResult.Ok -> {
                    _state.update { it.copy(message = "Đã nối: $source -$relation-> $target") }
                    refresh()
                }
                is AuraResult.Failed -> {
                    _state.update { it.copy(isLoading = false, error = "Lỗi tạo quan hệ: ${res.error}") }
                }
            }
        }
    }

    fun deleteRelation(source: String, relation: String, target: String) {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            when (val res = repository.deleteRelation(source, relation, target)) {
                is AuraResult.Ok -> {
                    _state.update { it.copy(message = "Đã xóa quan hệ giữa $source và $target") }
                    refresh()
                }
                is AuraResult.Failed -> {
                    _state.update { it.copy(isLoading = false, error = "Lỗi xóa quan hệ: ${res.error}") }
                }
            }
        }
    }

    fun deleteEpisode(id: Int) {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            when (val res = repository.deleteEpisode(id)) {
                is AuraResult.Ok -> {
                    _state.update { it.copy(message = "Đã xóa sự kiện #$id") }
                    refresh()
                }
                is AuraResult.Failed -> {
                    _state.update { it.copy(isLoading = false, error = "Lỗi xóa sự kiện: ${res.error}") }
                }
            }
        }
    }

    fun purge(target: String = "all", category: String? = null) {
        viewModelScope.launch {
            _state.update { it.copy(isLoading = true, error = null) }
            when (val res = repository.purgeMemory(target = target, category = category)) {
                is AuraResult.Ok -> {
                    _state.update { it.copy(message = "Toàn bộ dữ liệu ký ức ($target) đã được xóa hoàn toàn.") }
                    refresh()
                }
                is AuraResult.Failed -> {
                    _state.update { it.copy(isLoading = false, error = "Lỗi dọn dẹp ký ức: ${res.error}") }
                }
            }
        }
    }

    companion object {
        fun factory(repository: AuraRepository): ViewModelProvider.Factory =
            object : ViewModelProvider.Factory {
                @Suppress("UNCHECKED_CAST")
                override fun <T : ViewModel> create(modelClass: Class<T>): T =
                    MemoryHubViewModel(repository) as T
            }
    }
}
