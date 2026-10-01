package com.aura.companion.ui.hub

import com.aura.companion.data.AuraRepository
import com.aura.companion.data.settings.AuraSettings
import com.aura.companion.data.settings.FakeSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class MemoryHubViewModelTest {

    private lateinit var server: MockWebServer
    private lateinit var settings: FakeSettings
    private lateinit var repository: AuraRepository
    private lateinit var viewModel: MemoryHubViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(Dispatchers.Unconfined)
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.path.orEmpty()
                return when {
                    path.startsWith("/api/memory/overview") -> {
                        MockResponse()
                            .setResponseCode(200)
                            .setBody(
                                """
                                {
                                    "total_facts": 3,
                                    "total_entities": 2,
                                    "total_relations": 1,
                                    "total_episodes": 1,
                                    "categories": {"profile": 2, "preferences": 1}
                                }
                                """.trimIndent(),
                            )
                    }
                    path.startsWith("/api/memory/facts") && request.method == "GET" -> {
                        MockResponse()
                            .setResponseCode(200)
                            .setBody(
                                """
                                {
                                    "facts": [
                                        {"id": 1, "key": "user_name", "value": "Hoàn Thiện", "category": "profile", "source": "user"},
                                        {"id": 2, "key": "favorite_tea", "value": "Oolong", "category": "preferences", "source": "user"},
                                        {"id": 3, "key": "os_host", "value": "Windows 11", "category": "profile", "source": "system"}
                                    ],
                                    "count": 3
                                }
                                """.trimIndent(),
                            )
                    }
                    path.startsWith("/api/memory/facts") && request.method == "POST" -> {
                        MockResponse().setResponseCode(200).setBody("""{"ok": true}""")
                    }
                    path.startsWith("/api/memory/facts/") && request.method == "DELETE" -> {
                        MockResponse().setResponseCode(200).setBody("""{"ok": true, "deleted": true}""")
                    }
                    path.startsWith("/api/memory/graph") && request.method == "GET" -> {
                        MockResponse()
                            .setResponseCode(200)
                            .setBody(
                                """
                                {
                                    "entities": [
                                        {"id": 1, "name": "Hoàn Thiện", "entity_type": "PERSON", "description": "Lead Creator"},
                                        {"id": 2, "name": "Aura", "entity_type": "PROJECT", "description": "AI Assistant"}
                                    ],
                                    "relations": [
                                        {"id": 1, "source": "Hoàn Thiện", "relation": "CREATES", "target": "Aura", "confidence": 1.0}
                                    ],
                                    "stats": {"total_entities": 2, "total_relations": 1}
                                }
                                """.trimIndent(),
                            )
                    }
                    path.startsWith("/api/memory/entities") && request.method == "POST" -> {
                        MockResponse().setResponseCode(200).setBody("""{"ok": true}""")
                    }
                    path.startsWith("/api/memory/entities/") && request.method == "DELETE" -> {
                        MockResponse().setResponseCode(200).setBody("""{"ok": true, "deleted": true}""")
                    }
                    path.startsWith("/api/memory/relations") && request.method == "POST" -> {
                        MockResponse().setResponseCode(200).setBody("""{"ok": true}""")
                    }
                    path.startsWith("/api/memory/relations") && request.method == "DELETE" -> {
                        MockResponse().setResponseCode(200).setBody("""{"ok": true, "deleted": true}""")
                    }
                    path.startsWith("/api/memory/episodes") && request.method == "GET" -> {
                        MockResponse()
                            .setResponseCode(200)
                            .setBody(
                                """
                                {
                                    "episodes": [
                                        {"id": 1, "content": "Thảo luận về nâng cấp Knowledge Graph", "category": "architecture", "confidence": 1.0}
                                    ],
                                    "count": 1
                                }
                                """.trimIndent(),
                            )
                    }
                    path.startsWith("/api/memory/episodes/") && request.method == "DELETE" -> {
                        MockResponse().setResponseCode(200).setBody("""{"ok": true, "deleted": true}""")
                    }
                    path.startsWith("/api/memory/purge") && request.method == "POST" -> {
                        MockResponse().setResponseCode(200).setBody("""{"ok": true, "purged": "all"}""")
                    }
                    else -> MockResponse().setResponseCode(404)
                }
            }
        }
        server.start()

        settings = FakeSettings(
            serverUrl = server.url("/").toString(),
            authToken = "test-token",
        )
        repository = AuraRepository(settings)
        viewModel = MemoryHubViewModel(repository)
    }

    @After
    fun tearDown() {
        server.shutdown()
        Dispatchers.resetMain()
    }

    @Test
    fun `initial refresh populates overview facts graph and episodes`() = runBlocking {
        // Allow background refresh to complete
        Thread.sleep(200)

        val state = viewModel.state.value
        assertEquals(3, state.overview.totalFacts)
        assertEquals(2, state.overview.totalEntities)
        assertEquals(1, state.overview.totalRelations)
        assertEquals(3, state.facts.size)
        assertEquals(2, state.graph.entities.size)
        assertEquals(1, state.graph.relations.size)
        assertEquals(1, state.episodes.size)
    }

    @Test
    fun `search filtering filters facts and entities correctly`() = runBlocking {
        Thread.sleep(200)

        viewModel.setSearchQuery("Oolong")
        val state = viewModel.state.value
        assertEquals(1, state.filteredFacts.size)
        assertEquals("favorite_tea", state.filteredFacts.first().key)

        viewModel.setSearchQuery("Aura")
        val stateEntity = viewModel.state.value
        assertEquals(1, stateEntity.filteredEntities.size)
        assertEquals("Aura", stateEntity.filteredEntities.first().name)
    }

    @Test
    fun `category filtering filters facts by category`() = runBlocking {
        Thread.sleep(200)

        viewModel.setCategory("preferences")
        val state = viewModel.state.value
        assertEquals(1, state.filteredFacts.size)
        assertEquals("favorite_tea", state.filteredFacts.first().key)
    }

    @Test
    fun `tab switching changes selectedTab`() {
        viewModel.setTab(1)
        assertEquals(1, viewModel.state.value.selectedTab)
        viewModel.setTab(2)
        assertEquals(2, viewModel.state.value.selectedTab)
    }

    @Test
    fun `upsert and delete fact triggers action messages`() = runBlocking {
        viewModel.upsertFact("project_name", "AURA", "work")
        Thread.sleep(200)
        assertNotNull(viewModel.state.value.message)
        assertTrue(viewModel.state.value.message!!.contains("project_name"))

        viewModel.deleteFact("project_name")
        Thread.sleep(200)
        assertNotNull(viewModel.state.value.message)
        assertTrue(viewModel.state.value.message!!.contains("project_name"))
    }

    @Test
    fun `purge all triggers clean success message`() = runBlocking {
        viewModel.purge("all")
        Thread.sleep(200)
        assertNotNull(viewModel.state.value.message)
        assertTrue(viewModel.state.value.message!!.contains("xóa hoàn toàn"))
    }
}
