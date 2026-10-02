package com.aura.companion.accessibility

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class DeviceTaskDispatcherTest {

    private class FakeDeviceTaskHandler(
        var permissions: Set<String> = setOf(
            android.Manifest.permission.SEND_SMS,
            android.Manifest.permission.READ_SMS,
            android.Manifest.permission.READ_CALENDAR,
            android.Manifest.permission.WRITE_CALENDAR,
            android.Manifest.permission.READ_CONTACTS,
        )
    ) : DeviceTaskHandler {

        override fun hasPermission(permission: String): Boolean = permission in permissions

        override suspend fun sendSms(recipient: String, message: String): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("recipient", recipient)
                put("status", "sent")
                put("parts", 1)
            }
        )

        override suspend fun readSms(limit: Int, query: String): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("count", 1)
                put("messages", buildJsonArray {
                    add(buildJsonObject {
                        put("address", "+123456789")
                        put("body", "Test message")
                    })
                })
            }
        )

        override suspend fun createCalendarEvent(
            title: String,
            startTime: String,
            endTime: String,
            description: String
        ): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("event_id", "evt_123")
                put("title", title)
                put("start_time", startTime)
                put("status", "created")
            }
        )

        override suspend fun listCalendarEvents(startDate: String, limit: Int): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("count", 1)
                put("events", buildJsonArray {
                    add(buildJsonObject {
                        put("id", "evt_123")
                        put("title", "Standup")
                    })
                })
            }
        )

        override suspend fun searchContacts(query: String, limit: Int): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("count", 1)
                put("contacts", buildJsonArray {
                    add(buildJsonObject {
                        put("name", "Alice")
                        put("phone", "+123456789")
                    })
                })
            }
        )

        override suspend fun setAlarm(
            hour: Int,
            minute: Int,
            label: String,
            repeatDays: List<Int>
        ): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("alarm_id", "alarm_test_123")
                put("hour", hour)
                put("minute", minute)
                put("formatted_time", String.format("%02d:%02d", hour, minute))
                put("label", label)
                put("status", "scheduled")
            }
        )

        override suspend fun listAlarms(): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("count", 1)
                put("alarms", buildJsonArray {
                    add(buildJsonObject {
                        put("id", "alarm_test_123")
                        put("hour", 7)
                        put("minute", 0)
                        put("formatted_time", "07:00")
                        put("label", "Báo thức sáng")
                        put("is_enabled", true)
                    })
                })
            }
        )

        override suspend fun cancelAlarm(alarmId: String): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("alarm_id", alarmId)
                put("deleted", true)
                put("status", "canceled")
            }
        )

        override suspend fun setClipboard(text: String): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("status", "copied")
                put("length", text.length)
                put("preview", text.take(60))
            }
        )

        override suspend fun getClipboard(): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("has_clip", true)
                put("text", "mock_clipboard_content")
                put("length", 22)
            }
        )

        override suspend fun toggleFlashlight(enabled: Boolean?): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("flashlight_enabled", enabled ?: true)
                put("status", if (enabled == false) "off" else "on")
            }
        )

        override suspend fun getDeviceHealth(): Result<JsonObject> = Result.success(
            buildJsonObject {
                put("battery_level", 85)
                put("is_charging", true)
                put("available_ram_mb", 3500)
                put("total_ram_mb", 8192)
                put("low_memory", false)
                put("storage_free_gb", 45.2)
                put("storage_total_gb", 128.0)
                put("uptime_seconds", 36000L)
                put("status", "healthy")
            }
        )
    }

    private fun directive(tool: String, arguments: JsonObject): ToolCallDirective =
        ToolCallDirective(
            toolCallId = "call_test_1",
            tool = tool,
            arguments = arguments,
        )

    @Test
    fun `task catalog validates required and optional arguments`() {
        val validSms = directive("android.send_sms", buildJsonObject {
            put("recipient", "+123456")
            put("message", "hello")
        })
        assertTrue(DeviceTaskToolCatalog.validate(validSms) is DeviceToolCatalog.Validation.Ok)

        val missingMsg = directive("android.send_sms", buildJsonObject {
            put("recipient", "+123456")
        })
        val validation = DeviceTaskToolCatalog.validate(missingMsg)
        assertTrue(validation is DeviceToolCatalog.Validation.BadArguments)

        val foreignArg = directive("android.send_sms", buildJsonObject {
            put("recipient", "+123456")
            put("message", "hello")
            put("extra", "invalid")
        })
        assertTrue(DeviceTaskToolCatalog.validate(foreignArg) is DeviceToolCatalog.Validation.BadArguments)
    }

    @Test
    fun `blocked permission returned when handler lacks permission`() = runBlocking {
        val fakeHandler = FakeDeviceTaskHandler(permissions = emptySet())
        val dispatcher = DeviceTaskDispatcher(fakeHandler)

        val call = directive("android.send_sms", buildJsonObject {
            put("recipient", "+123456")
            put("message", "hello")
        })

        val report = dispatcher.execute(call)
        assertFalse(report.ok)
        assertEquals("BLOCKED_PERMISSION", report.error?.code)
    }

    @Test
    fun `send_sms executes successfully with verified postcondition`() = runBlocking {
        val fakeHandler = FakeDeviceTaskHandler()
        val dispatcher = DeviceTaskDispatcher(fakeHandler)

        val call = directive("android.send_sms", buildJsonObject {
            put("recipient", "+123456")
            put("message", "hello")
        })

        val report = dispatcher.execute(call)
        assertTrue(report.ok)
        assertEquals("sent", report.result?.get("status")?.jsonPrimitive?.contentOrNull)
        assertNotNull(report.postcondition)
        assertEquals(true, report.postcondition?.get("verified")?.jsonPrimitive?.contentOrNull?.toBoolean())
        assertEquals("send_sms", report.observation?.kind)
    }

    @Test
    fun `create_calendar_event executes with verified postcondition`() = runBlocking {
        val fakeHandler = FakeDeviceTaskHandler()
        val dispatcher = DeviceTaskDispatcher(fakeHandler)

        val call = directive("android.create_calendar_event", buildJsonObject {
            put("title", "Project Review")
            put("start_time", "2026-10-02T10:00:00")
        })

        val report = dispatcher.execute(call)
        assertTrue(report.ok)
        assertEquals("created", report.result?.get("status")?.jsonPrimitive?.contentOrNull)
        assertEquals(true, report.postcondition?.get("verified")?.jsonPrimitive?.contentOrNull?.toBoolean())
        assertEquals("create_calendar_event", report.postcondition?.get("action")?.jsonPrimitive?.contentOrNull)
    }

    @Test
    fun `search_contacts executes and returns contacts list`() = runBlocking {
        val fakeHandler = FakeDeviceTaskHandler()
        val dispatcher = DeviceTaskDispatcher(fakeHandler)

        val call = directive("android.search_contacts", buildJsonObject {
            put("query", "Alice")
        })

        val report = dispatcher.execute(call)
        assertTrue(report.ok)
        assertEquals(1, (report.result?.get("count")?.jsonPrimitive?.contentOrNull)?.toInt())
        assertEquals("search_contacts", report.observation?.kind)
    }

    @Test
    fun `set_alarm executes with verified postcondition`() = runBlocking {
        val fakeHandler = FakeDeviceTaskHandler()
        val dispatcher = DeviceTaskDispatcher(fakeHandler)

        val call = directive("android.set_alarm", buildJsonObject {
            put("hour", 7)
            put("minute", 30)
            put("label", "Dậy đi làm")
        })

        val report = dispatcher.execute(call)
        assertTrue(report.ok)
        assertEquals("scheduled", report.result?.get("status")?.jsonPrimitive?.contentOrNull)
        assertEquals("07:30", report.result?.get("formatted_time")?.jsonPrimitive?.contentOrNull)
        assertNotNull(report.postcondition)
        assertEquals(true, report.postcondition?.get("verified")?.jsonPrimitive?.contentOrNull?.toBoolean())
        assertEquals("set_alarm", report.postcondition?.get("action")?.jsonPrimitive?.contentOrNull)
        assertEquals("07:30", report.postcondition?.get("formatted_time")?.jsonPrimitive?.contentOrNull)
    }

    @Test
    fun `list_alarms returns alarm list`() = runBlocking {
        val fakeHandler = FakeDeviceTaskHandler()
        val dispatcher = DeviceTaskDispatcher(fakeHandler)

        val call = directive("android.list_alarms", buildJsonObject {})

        val report = dispatcher.execute(call)
        assertTrue(report.ok)
        assertEquals(1, report.result?.get("count")?.jsonPrimitive?.contentOrNull?.toInt())
    }

    @Test
    fun `cancel_alarm executes with verified postcondition`() = runBlocking {
        val fakeHandler = FakeDeviceTaskHandler()
        val dispatcher = DeviceTaskDispatcher(fakeHandler)

        val call = directive("android.cancel_alarm", buildJsonObject {
            put("alarm_id", "alarm_123")
        })

        val report = dispatcher.execute(call)
        assertTrue(report.ok)
        assertEquals("canceled", report.result?.get("status")?.jsonPrimitive?.contentOrNull)
        assertEquals(true, report.postcondition?.get("verified")?.jsonPrimitive?.contentOrNull?.toBoolean())
        assertEquals("cancel_alarm", report.postcondition?.get("action")?.jsonPrimitive?.contentOrNull)
        assertEquals("alarm_123", report.postcondition?.get("alarm_id")?.jsonPrimitive?.contentOrNull)
    }

    @Test
    fun `set_clipboard executes with verified postcondition`() = runBlocking {
        val fakeHandler = FakeDeviceTaskHandler()
        val dispatcher = DeviceTaskDispatcher(fakeHandler)

        val call = directive("android.set_clipboard", buildJsonObject {
            put("text", "https://aura.ai/docs")
        })

        val report = dispatcher.execute(call)
        assertTrue(report.ok)
        assertEquals("copied", report.result?.get("status")?.jsonPrimitive?.contentOrNull)
        assertEquals(true, report.postcondition?.get("verified")?.jsonPrimitive?.contentOrNull?.toBoolean())
        assertEquals("set_clipboard", report.postcondition?.get("action")?.jsonPrimitive?.contentOrNull)
        assertEquals(20, report.postcondition?.get("length")?.jsonPrimitive?.contentOrNull?.toInt())
    }

    @Test
    fun `get_clipboard returns clipboard text`() = runBlocking {
        val fakeHandler = FakeDeviceTaskHandler()
        val dispatcher = DeviceTaskDispatcher(fakeHandler)

        val call = directive("android.get_clipboard", buildJsonObject {})

        val report = dispatcher.execute(call)
        assertTrue(report.ok)
        assertEquals(true, report.result?.get("has_clip")?.jsonPrimitive?.contentOrNull?.toBoolean())
        assertEquals("mock_clipboard_content", report.result?.get("text")?.jsonPrimitive?.contentOrNull)
    }

    @Test
    fun `toggle_flashlight executes with verified postcondition`() = runBlocking {
        val fakeHandler = FakeDeviceTaskHandler()
        val dispatcher = DeviceTaskDispatcher(fakeHandler)

        val call = directive("android.toggle_flashlight", buildJsonObject {
            put("enabled", "true")
        })

        val report = dispatcher.execute(call)
        assertTrue(report.ok)
        assertEquals("on", report.result?.get("status")?.jsonPrimitive?.contentOrNull)
        assertEquals(true, report.result?.get("flashlight_enabled")?.jsonPrimitive?.contentOrNull?.toBoolean())
        assertNotNull(report.postcondition)
        assertEquals(true, report.postcondition?.get("verified")?.jsonPrimitive?.contentOrNull?.toBoolean())
        assertEquals("toggle_flashlight", report.postcondition?.get("action")?.jsonPrimitive?.contentOrNull)
        assertEquals(true, report.postcondition?.get("flashlight_enabled")?.jsonPrimitive?.contentOrNull?.toBoolean())
    }

    @Test
    fun `get_device_health executes and returns health metrics`() = runBlocking {
        val fakeHandler = FakeDeviceTaskHandler()
        val dispatcher = DeviceTaskDispatcher(fakeHandler)

        val call = directive("android.get_device_health", buildJsonObject {})

        val report = dispatcher.execute(call)
        assertTrue(report.ok)
        assertEquals(85, report.result?.get("battery_level")?.jsonPrimitive?.contentOrNull?.toInt())
        assertEquals(true, report.result?.get("is_charging")?.jsonPrimitive?.contentOrNull?.toBoolean())
        assertEquals(3500, report.result?.get("available_ram_mb")?.jsonPrimitive?.contentOrNull?.toInt())
        assertEquals(8192, report.result?.get("total_ram_mb")?.jsonPrimitive?.contentOrNull?.toInt())
        assertEquals("healthy", report.result?.get("status")?.jsonPrimitive?.contentOrNull)
    }
}
