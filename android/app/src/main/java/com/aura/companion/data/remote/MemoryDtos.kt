package com.aura.companion.data.remote

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject

/**
 * Wire DTOs for AURA Deep Entity Memory & Knowledge Graph API.
 * Exactly matches `server/routes/memory.py`.
 */

@Serializable
data class MemoryOverviewDto(
    @SerialName("total_facts") val totalFacts: Int = 0,
    @SerialName("total_entities") val totalEntities: Int = 0,
    @SerialName("total_relations") val totalRelations: Int = 0,
    @SerialName("total_episodes") val totalEpisodes: Int = 0,
    val categories: Map<String, Int> = emptyMap(),
)

@Serializable
data class MemoryFactDto(
    val id: Int = 0,
    val key: String,
    val value: String,
    val category: String? = "profile",
    val source: String? = "user",
    @SerialName("created_at") val createdAt: Double = 0.0,
    @SerialName("updated_at") val updatedAt: Double = 0.0,
)

@Serializable
data class MemoryFactsResponseDto(
    val facts: List<MemoryFactDto> = emptyList(),
    val count: Int = 0,
)

@Serializable
data class FactUpsertRequestDto(
    val key: String,
    val value: String,
    val category: String = "profile",
)

@Serializable
data class MemoryEntityDto(
    val id: Int = 0,
    val name: String,
    @SerialName("entity_type") val entityType: String = "CONCEPT",
    val description: String? = null,
    val properties: Map<String, JsonElement> = emptyMap(),
    @SerialName("created_at") val createdAt: Double = 0.0,
    @SerialName("updated_at") val updatedAt: Double = 0.0,
)

@Serializable
data class MemoryRelationDto(
    val id: Int = 0,
    val source: String,
    val relation: String,
    val target: String,
    val confidence: Double = 1.0,
    @SerialName("source_type") val sourceType: String = "user",
    @SerialName("created_at") val createdAt: Double = 0.0,
)

@Serializable
data class MemoryGraphDto(
    val entities: List<MemoryEntityDto> = emptyList(),
    val relations: List<MemoryRelationDto> = emptyList(),
    val stats: Map<String, Int> = emptyMap(),
)

@Serializable
data class EntityCreateRequestDto(
    val name: String,
    @SerialName("entity_type") val entityType: String = "CONCEPT",
    val description: String = "",
    val properties: Map<String, JsonElement> = emptyMap(),
)

@Serializable
data class RelationCreateRequestDto(
    val source: String,
    val relation: String,
    val target: String,
    val confidence: Double = 1.0,
)

@Serializable
data class MemoryEpisodeDto(
    val id: Int = 0,
    val content: String = "",
    val category: String = "general",
    val source: String = "conversation",
    val importance: Double = 0.0,
    val confidence: Double = 1.0,
    @SerialName("occurred_at") val occurredAt: Double = 0.0,
    @SerialName("created_at") val createdAt: Double = 0.0,
)

@Serializable
data class MemoryEpisodesResponseDto(
    val episodes: List<MemoryEpisodeDto> = emptyList(),
    val count: Int = 0,
)

@Serializable
data class PurgeMemoryRequestDto(
    val target: String = "all",
    val category: String? = null,
)

@Serializable
data class MemoryActionResponseDto(
    val ok: Boolean = false,
    val deleted: Boolean? = null,
    val key: String? = null,
    val name: String? = null,
    val purged: String? = null,
    val message: String? = null,
)
