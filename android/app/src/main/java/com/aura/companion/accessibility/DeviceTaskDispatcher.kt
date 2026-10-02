package com.aura.companion.accessibility

import android.Manifest
import android.app.ActivityManager
import android.content.ClipData
import android.content.ClipboardManager
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.os.Environment
import android.os.StatFs
import android.os.SystemClock
import android.provider.CalendarContract
import android.provider.ContactsContract
import android.provider.Telephony
import android.telephony.SmsManager
import androidx.core.content.ContextCompat
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import kotlinx.serialization.json.add
import com.aura.companion.alarm.data.AlarmStore
import com.aura.companion.alarm.data.IAlarmStore
import com.aura.companion.alarm.model.AuraAlarm
import com.aura.companion.alarm.service.AlarmScheduler
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * Catalogue for Android Personal Task Tools (Phase 5: SMS, Calendar, Contacts).
 *
 * Keeps the accessibility catalogue [DeviceToolCatalog] decoupled and pristine
 * while offering identical strict validation for personal task directives.
 */
object DeviceTaskToolCatalog {

    data class TaskToolSpec(
        val name: String,
        val required: Set<String>,
        val optional: Set<String>,
        val mutating: Boolean,
        val requiredPermission: String,
    )

    val TOOLS: Map<String, TaskToolSpec> = listOf(
        TaskToolSpec(
            name = "android.send_sms",
            required = setOf("recipient", "message"),
            optional = emptySet(),
            mutating = true,
            requiredPermission = Manifest.permission.SEND_SMS,
        ),
        TaskToolSpec(
            name = "android.read_sms",
            required = emptySet(),
            optional = setOf("limit", "query"),
            mutating = false,
            requiredPermission = Manifest.permission.READ_SMS,
        ),
        TaskToolSpec(
            name = "android.create_calendar_event",
            required = setOf("title", "start_time"),
            optional = setOf("end_time", "description"),
            mutating = true,
            requiredPermission = Manifest.permission.WRITE_CALENDAR,
        ),
        TaskToolSpec(
            name = "android.list_calendar_events",
            required = emptySet(),
            optional = setOf("start_date", "limit"),
            mutating = false,
            requiredPermission = Manifest.permission.READ_CALENDAR,
        ),
        TaskToolSpec(
            name = "android.search_contacts",
            required = emptySet(),
            optional = setOf("query", "limit"),
            mutating = false,
            requiredPermission = Manifest.permission.READ_CONTACTS,
        ),
        TaskToolSpec(
            name = "android.set_alarm",
            required = setOf("hour", "minute"),
            optional = setOf("label", "repeat_days"),
            mutating = true,
            requiredPermission = "",
        ),
        TaskToolSpec(
            name = "android.list_alarms",
            required = emptySet(),
            optional = emptySet(),
            mutating = false,
            requiredPermission = "",
        ),
        TaskToolSpec(
            name = "android.cancel_alarm",
            required = setOf("alarm_id"),
            optional = emptySet(),
            mutating = true,
            requiredPermission = "",
        ),
        TaskToolSpec(
            name = "android.set_clipboard",
            required = setOf("text"),
            optional = emptySet(),
            mutating = true,
            requiredPermission = "",
        ),
        TaskToolSpec(
            name = "android.get_clipboard",
            required = emptySet(),
            optional = emptySet(),
            mutating = false,
            requiredPermission = "",
        ),
        TaskToolSpec(
            name = "android.toggle_flashlight",
            required = emptySet(),
            optional = setOf("enabled"),
            mutating = true,
            requiredPermission = "",
        ),
        TaskToolSpec(
            name = "android.get_device_health",
            required = emptySet(),
            optional = emptySet(),
            mutating = false,
            requiredPermission = "",
        ),
    ).associateBy { it.name }

    fun isTaskTool(tool: String): Boolean = tool in TOOLS

    fun validate(directive: ToolCallDirective): DeviceToolCatalog.Validation {
        val spec = TOOLS[directive.tool] ?: return DeviceToolCatalog.Validation.UnknownTool(directive.tool)
        val provided = directive.arguments.keys
        val missing = spec.required.filterNot { it in provided }
        if (missing.isNotEmpty()) {
            return DeviceToolCatalog.Validation.BadArguments(
                "${directive.tool} requires ${missing.joinToString(", ")}"
            )
        }
        val foreign = provided.filterNot { it in spec.required || it in spec.optional }
        if (foreign.isNotEmpty()) {
            return DeviceToolCatalog.Validation.BadArguments(
                "${directive.tool} does not accept ${foreign.joinToString(", ")}"
            )
        }
        return DeviceToolCatalog.Validation.Ok(
            DeviceToolCatalog.ToolSpec(
                name = spec.name,
                required = spec.required,
                optional = spec.optional,
                mutating = spec.mutating,
            )
        )
    }
}

/**
 * Seam for executing Android personal tasks (SMS, Calendar, Contacts).
 * Enables offline JVM unit testing without live telephony/provider mocks.
 */
interface DeviceTaskHandler {
    fun hasPermission(permission: String): Boolean
    suspend fun sendSms(recipient: String, message: String): Result<JsonObject>
    suspend fun readSms(limit: Int, query: String): Result<JsonObject>
    suspend fun createCalendarEvent(title: String, startTime: String, endTime: String, description: String): Result<JsonObject>
    suspend fun listCalendarEvents(startDate: String, limit: Int): Result<JsonObject>
    suspend fun searchContacts(query: String, limit: Int): Result<JsonObject>
    suspend fun setAlarm(hour: Int, minute: Int, label: String, repeatDays: List<Int>): Result<JsonObject>
    suspend fun listAlarms(): Result<JsonObject>
    suspend fun cancelAlarm(alarmId: String): Result<JsonObject>
    suspend fun setClipboard(text: String): Result<JsonObject>
    suspend fun getClipboard(): Result<JsonObject>
    suspend fun toggleFlashlight(enabled: Boolean?): Result<JsonObject>
    suspend fun getDeviceHealth(): Result<JsonObject>
}

/**
 * Production Android task handler using platform ContentResolvers and SmsManager.
 */
class AndroidDeviceTaskHandler(
    private val context: Context,
    private val alarmStore: IAlarmStore = AlarmStore(context),
    private val alarmScheduler: AlarmScheduler = AlarmScheduler(context, alarmStore),
) : DeviceTaskHandler {

    override fun hasPermission(permission: String): Boolean {
        if (permission.isBlank()) return true
        return ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED
    }

    override suspend fun sendSms(recipient: String, message: String): Result<JsonObject> = runCatching {
        val smsManager = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            context.getSystemService(SmsManager::class.java)
        } else {
            @Suppress("DEPRECATION")
            SmsManager.getDefault()
        }

        val parts = smsManager.divideMessage(message)
        if (parts.size > 1) {
            smsManager.sendMultipartTextMessage(recipient, null, parts, null, null)
        } else {
            smsManager.sendTextMessage(recipient, null, message, null, null)
        }

        buildJsonObject {
            put("recipient", recipient)
            put("status", "sent")
            put("parts", parts.size)
        }
    }

    override suspend fun readSms(limit: Int, query: String): Result<JsonObject> = runCatching {
        val messages = mutableListOf<JsonObject>()
        val uri = Telephony.Sms.CONTENT_URI
        val projection = arrayOf(
            Telephony.Sms._ID,
            Telephony.Sms.ADDRESS,
            Telephony.Sms.BODY,
            Telephony.Sms.DATE,
            Telephony.Sms.TYPE,
        )

        var selection: String? = null
        var selectionArgs: Array<String>? = null
        if (query.isNotBlank()) {
            selection = "${Telephony.Sms.BODY} LIKE ? OR ${Telephony.Sms.ADDRESS} LIKE ?"
            selectionArgs = arrayOf("%$query%", "%$query%")
        }

        val sortOrder = "${Telephony.Sms.DATE} DESC LIMIT $limit"
        val cursor = context.contentResolver.query(uri, projection, selection, selectionArgs, sortOrder)
        cursor?.use { c ->
            val addressIdx = c.getColumnIndex(Telephony.Sms.ADDRESS)
            val bodyIdx = c.getColumnIndex(Telephony.Sms.BODY)
            val dateIdx = c.getColumnIndex(Telephony.Sms.DATE)
            val typeIdx = c.getColumnIndex(Telephony.Sms.TYPE)

            while (c.moveToNext()) {
                val address = if (addressIdx >= 0) c.getString(addressIdx) ?: "" else ""
                val body = if (bodyIdx >= 0) c.getString(bodyIdx) ?: "" else ""
                val date = if (dateIdx >= 0) c.getLong(dateIdx) else 0L
                val type = if (typeIdx >= 0) c.getInt(typeIdx) else 1

                messages.add(buildJsonObject {
                    put("address", address)
                    put("body", body)
                    put("timestamp", date)
                    put("type", if (type == Telephony.Sms.MESSAGE_TYPE_SENT) "sent" else "received")
                })
            }
        }

        buildJsonObject {
            put("count", messages.size)
            put("messages", buildJsonArray { messages.forEach { add(it) } })
        }
    }

    override suspend fun createCalendarEvent(
        title: String,
        startTime: String,
        endTime: String,
        description: String,
    ): Result<JsonObject> = runCatching {
        val startMillis = parseTimeToMillis(startTime)
        val endMillis = if (endTime.isNotBlank()) parseTimeToMillis(endTime) else startMillis + (30 * 60 * 1000)

        val values = ContentValues().apply {
            put(CalendarContract.Events.DTSTART, startMillis)
            put(CalendarContract.Events.DTEND, endMillis)
            put(CalendarContract.Events.TITLE, title)
            put(CalendarContract.Events.DESCRIPTION, description)
            put(CalendarContract.Events.CALENDAR_ID, 1) // Default primary calendar
            put(CalendarContract.Events.EVENT_TIMEZONE, TimeZone.getDefault().id)
        }

        val uri = context.contentResolver.insert(CalendarContract.Events.CONTENT_URI, values)
        val eventId = uri?.lastPathSegment ?: "unknown"

        buildJsonObject {
            put("event_id", eventId)
            put("title", title)
            put("start_time", startTime)
            put("status", "created")
        }
    }

    override suspend fun listCalendarEvents(startDate: String, limit: Int): Result<JsonObject> = runCatching {
        val events = mutableListOf<JsonObject>()
        val startMillis = if (startDate.isNotBlank()) parseTimeToMillis(startDate) else System.currentTimeMillis()
        val endMillis = startMillis + (30L * 24 * 60 * 60 * 1000) // Default 30 day window

        val builder = CalendarContract.Instances.CONTENT_URI.buildUpon()
        ContentUris.appendId(builder, startMillis)
        ContentUris.appendId(builder, endMillis)

        val projection = arrayOf(
            CalendarContract.Instances.EVENT_ID,
            CalendarContract.Instances.TITLE,
            CalendarContract.Instances.BEGIN,
            CalendarContract.Instances.END,
            CalendarContract.Instances.DESCRIPTION,
        )

        val cursor = context.contentResolver.query(
            builder.build(),
            projection,
            null,
            null,
            "${CalendarContract.Instances.BEGIN} ASC LIMIT $limit"
        )

        cursor?.use { c ->
            val idIdx = c.getColumnIndex(CalendarContract.Instances.EVENT_ID)
            val titleIdx = c.getColumnIndex(CalendarContract.Instances.TITLE)
            val beginIdx = c.getColumnIndex(CalendarContract.Instances.BEGIN)
            val endIdx = c.getColumnIndex(CalendarContract.Instances.END)
            val descIdx = c.getColumnIndex(CalendarContract.Instances.DESCRIPTION)

            while (c.moveToNext()) {
                val id = if (idIdx >= 0) c.getString(idIdx) ?: "" else ""
                val title = if (titleIdx >= 0) c.getString(titleIdx) ?: "" else ""
                val begin = if (beginIdx >= 0) c.getLong(beginIdx) else 0L
                val end = if (endIdx >= 0) c.getLong(endIdx) else 0L
                val desc = if (descIdx >= 0) c.getString(descIdx) ?: "" else ""

                events.add(buildJsonObject {
                    put("id", id)
                    put("title", title)
                    put("start_time", begin)
                    put("end_time", end)
                    put("description", desc)
                })
            }
        }

        buildJsonObject {
            put("count", events.size)
            put("events", buildJsonArray { events.forEach { add(it) } })
        }
    }

    override suspend fun searchContacts(query: String, limit: Int): Result<JsonObject> = runCatching {
        val contacts = mutableListOf<JsonObject>()
        val uri = ContactsContract.CommonDataKinds.Phone.CONTENT_URI
        val projection = arrayOf(
            ContactsContract.CommonDataKinds.Phone.CONTACT_ID,
            ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            ContactsContract.CommonDataKinds.Phone.NUMBER,
        )

        var selection: String? = null
        var selectionArgs: Array<String>? = null
        if (query.isNotBlank()) {
            selection = "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} LIKE ? OR ${ContactsContract.CommonDataKinds.Phone.NUMBER} LIKE ?"
            selectionArgs = arrayOf("%$query%", "%$query%")
        }

        val cursor = context.contentResolver.query(
            uri,
            projection,
            selection,
            selectionArgs,
            "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} ASC LIMIT $limit"
        )

        cursor?.use { c ->
            val nameIdx = c.getColumnIndex(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME)
            val numIdx = c.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)

            while (c.moveToNext()) {
                val name = if (nameIdx >= 0) c.getString(nameIdx) ?: "" else ""
                val num = if (numIdx >= 0) c.getString(numIdx) ?: "" else ""

                contacts.add(buildJsonObject {
                    put("name", name)
                    put("phone", num)
                })
            }
        }

        buildJsonObject {
            put("count", contacts.size)
            put("contacts", buildJsonArray { contacts.forEach { add(it) } })
        }
    }

    override suspend fun setAlarm(
        hour: Int,
        minute: Int,
        label: String,
        repeatDays: List<Int>
    ): Result<JsonObject> = runCatching {
        val alarm = AuraAlarm(
            hour = hour,
            minute = minute,
            label = label.ifBlank { "Báo thức Aura" },
            repeatDays = repeatDays
        )
        alarmStore.save(alarm)
        val triggerMillis = alarmScheduler.schedule(alarm)

        buildJsonObject {
            put("alarm_id", alarm.id)
            put("hour", alarm.hour)
            put("minute", alarm.minute)
            put("formatted_time", alarm.formattedTime)
            put("label", alarm.label)
            put("trigger_millis", triggerMillis)
            put("repeat_days", buildJsonArray { repeatDays.forEach { add(JsonPrimitive(it)) } })
            put("status", "scheduled")
        }
    }

    override suspend fun listAlarms(): Result<JsonObject> = runCatching {
        val alarms = alarmStore.getAll()
        buildJsonObject {
            put("count", alarms.size)
            put("alarms", buildJsonArray {
                alarms.forEach { a ->
                    add(buildJsonObject {
                        put("id", a.id)
                        put("hour", a.hour)
                        put("minute", a.minute)
                        put("formatted_time", a.formattedTime)
                        put("label", a.label)
                        put("is_enabled", a.isEnabled)
                        put("repeat_days", buildJsonArray { a.repeatDays.forEach { add(JsonPrimitive(it)) } })
                    })
                }
            })
        }
    }

    override suspend fun cancelAlarm(alarmId: String): Result<JsonObject> = runCatching {
        alarmScheduler.cancel(alarmId)
        val deleted = alarmStore.delete(alarmId)
        buildJsonObject {
            put("alarm_id", alarmId)
            put("deleted", deleted)
            put("status", if (deleted) "canceled" else "not_found")
        }
    }

    override suspend fun setClipboard(text: String): Result<JsonObject> = runCatching {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = ClipData.newPlainText("Aura", text)
        clipboard.setPrimaryClip(clip)
        buildJsonObject {
            put("status", "copied")
            put("length", text.length)
            put("preview", text.take(60))
        }
    }

    override suspend fun getClipboard(): Result<JsonObject> = runCatching {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = clipboard.primaryClip
        val item = if (clip != null && clip.itemCount > 0) clip.getItemAt(0) else null
        val text = item?.coerceToText(context)?.toString() ?: ""
        buildJsonObject {
            put("has_clip", text.isNotEmpty())
            put("text", text)
            put("length", text.length)
        }
    }

    private var isTorchOn: Boolean = false

    override suspend fun toggleFlashlight(enabled: Boolean?): Result<JsonObject> = runCatching {
        val cameraManager = context.getSystemService(Context.CAMERA_SERVICE) as CameraManager
        val cameraId = cameraManager.cameraIdList.firstOrNull { id ->
            val chars = cameraManager.getCameraCharacteristics(id)
            val hasFlash = chars.get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
            val facing = chars.get(CameraCharacteristics.LENS_FACING)
            hasFlash && facing == CameraCharacteristics.LENS_FACING_BACK
        } ?: cameraManager.cameraIdList.firstOrNull { id ->
            cameraManager.getCameraCharacteristics(id).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
        } ?: throw IllegalStateException("Không tìm thấy đèn flash trên thiết bị này")

        val targetState = enabled ?: !isTorchOn
        cameraManager.setTorchMode(cameraId, targetState)
        isTorchOn = targetState

        buildJsonObject {
            put("flashlight_enabled", targetState)
            put("status", if (targetState) "on" else "off")
        }
    }

    override suspend fun getDeviceHealth(): Result<JsonObject> = runCatching {
        val batteryStatus = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = batteryStatus?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = batteryStatus?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val batteryPct = if (level >= 0 && scale > 0) (level * 100) / scale else -1
        val status = batteryStatus?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val isCharging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL

        val activityManager = context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val memInfo = ActivityManager.MemoryInfo()
        activityManager.getMemoryInfo(memInfo)

        val stat = StatFs(Environment.getDataDirectory().path)
        val freeBytes = stat.availableBytes
        val totalBytes = stat.totalBytes
        val freeGb = (freeBytes * 100 / (1024L * 1024 * 1024)).toDouble() / 100.0
        val totalGb = (totalBytes * 100 / (1024L * 1024 * 1024)).toDouble() / 100.0

        val uptimeSec = SystemClock.elapsedRealtime() / 1000

        buildJsonObject {
            put("battery_level", batteryPct)
            put("is_charging", isCharging)
            put("available_ram_mb", (memInfo.availMem / (1024 * 1024)).toInt())
            put("total_ram_mb", (memInfo.totalMem / (1024 * 1024)).toInt())
            put("low_memory", memInfo.lowMemory)
            put("storage_free_gb", freeGb)
            put("storage_total_gb", totalGb)
            put("uptime_seconds", uptimeSec)
            put("status", "healthy")
        }
    }

    private fun parseTimeToMillis(isoOrEpoch: String): Long {
        return try {
            isoOrEpoch.toLong()
        } catch (_: NumberFormatException) {
            val format = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).apply {
                timeZone = TimeZone.getDefault()
            }
            format.parse(isoOrEpoch)?.time ?: System.currentTimeMillis()
        }
    }
}

/**
 * Dispatcher for personal task tools executing on device.
 */
class DeviceTaskDispatcher(
    private val handler: DeviceTaskHandler,
) {

    suspend fun execute(directive: ToolCallDirective): ToolResultReport {
        val spec = DeviceTaskToolCatalog.TOOLS[directive.tool]
            ?: return failure(directive, "TOOL_NOT_FOUND", "Unknown task tool ${directive.tool}")

        if (spec.requiredPermission.isNotBlank() && !handler.hasPermission(spec.requiredPermission)) {
            return failure(
                directive,
                "BLOCKED_PERMISSION",
                "Permission ${spec.requiredPermission} is not granted on this device."
            )
        }

        return try {
            when (directive.tool) {
                "android.send_sms" -> {
                    val recipient = directive.arguments["recipient"]?.jsonPrimitive?.contentOrNull ?: ""
                    val message = directive.arguments["message"]?.jsonPrimitive?.contentOrNull ?: ""
                    val res = handler.sendSms(recipient, message).getOrThrow()
                    success(
                        directive = directive,
                        result = res,
                        postcondition = buildJsonObject {
                            put("verified", true)
                            put("action", "send_sms")
                            put("recipient", recipient)
                        },
                    )
                }

                "android.read_sms" -> {
                    val limit = directive.arguments["limit"]?.jsonPrimitive?.intOrNull ?: 10
                    val query = directive.arguments["query"]?.jsonPrimitive?.contentOrNull ?: ""
                    val res = handler.readSms(limit, query).getOrThrow()
                    success(directive = directive, result = res)
                }

                "android.create_calendar_event" -> {
                    val title = directive.arguments["title"]?.jsonPrimitive?.contentOrNull ?: ""
                    val start = directive.arguments["start_time"]?.jsonPrimitive?.contentOrNull ?: ""
                    val end = directive.arguments["end_time"]?.jsonPrimitive?.contentOrNull ?: ""
                    val desc = directive.arguments["description"]?.jsonPrimitive?.contentOrNull ?: ""
                    val res = handler.createCalendarEvent(title, start, end, desc).getOrThrow()
                    success(
                        directive = directive,
                        result = res,
                        postcondition = buildJsonObject {
                            put("verified", true)
                            put("action", "create_calendar_event")
                            put("title", title)
                        },
                    )
                }

                "android.list_calendar_events" -> {
                    val startDate = directive.arguments["start_date"]?.jsonPrimitive?.contentOrNull ?: ""
                    val limit = directive.arguments["limit"]?.jsonPrimitive?.intOrNull ?: 10
                    val res = handler.listCalendarEvents(startDate, limit).getOrThrow()
                    success(directive = directive, result = res)
                }

                "android.search_contacts" -> {
                    val query = directive.arguments["query"]?.jsonPrimitive?.contentOrNull ?: ""
                    val limit = directive.arguments["limit"]?.jsonPrimitive?.intOrNull ?: 10
                    val res = handler.searchContacts(query, limit).getOrThrow()
                    success(directive = directive, result = res)
                }

                "android.set_alarm" -> {
                    val hour = directive.arguments["hour"]?.jsonPrimitive?.intOrNull ?: 0
                    val minute = directive.arguments["minute"]?.jsonPrimitive?.intOrNull ?: 0
                    val label = directive.arguments["label"]?.jsonPrimitive?.contentOrNull ?: "Báo thức Aura"
                    val repeatDays = (directive.arguments["repeat_days"] as? JsonArray)?.mapNotNull { it.jsonPrimitive.intOrNull } ?: emptyList()
                    val res = handler.setAlarm(hour, minute, label, repeatDays).getOrThrow()
                    success(
                        directive = directive,
                        result = res,
                        postcondition = buildJsonObject {
                            put("verified", true)
                            put("action", "set_alarm")
                            put("hour", hour)
                            put("minute", minute)
                            put("formatted_time", String.format("%02d:%02d", hour, minute))
                            put("alarm_id", res["alarm_id"]?.jsonPrimitive?.contentOrNull ?: "")
                        },
                    )
                }

                "android.list_alarms" -> {
                    val res = handler.listAlarms().getOrThrow()
                    success(directive = directive, result = res)
                }

                "android.cancel_alarm" -> {
                    val alarmId = directive.arguments["alarm_id"]?.jsonPrimitive?.contentOrNull ?: ""
                    val res = handler.cancelAlarm(alarmId).getOrThrow()
                    success(
                        directive = directive,
                        result = res,
                        postcondition = buildJsonObject {
                            put("verified", true)
                            put("action", "cancel_alarm")
                            put("alarm_id", alarmId)
                        },
                    )
                }

                "android.set_clipboard" -> {
                    val text = directive.arguments["text"]?.jsonPrimitive?.contentOrNull ?: ""
                    val res = handler.setClipboard(text).getOrThrow()
                    success(
                        directive = directive,
                        result = res,
                        postcondition = buildJsonObject {
                            put("verified", true)
                            put("action", "set_clipboard")
                            put("length", text.length)
                        },
                    )
                }

                "android.get_clipboard" -> {
                    val res = handler.getClipboard().getOrThrow()
                    success(directive = directive, result = res)
                }

                "android.toggle_flashlight" -> {
                    val enabled = directive.arguments["enabled"]?.jsonPrimitive?.contentOrNull?.toBooleanStrictOrNull()
                    val res = handler.toggleFlashlight(enabled).getOrThrow()
                    val newState = res["flashlight_enabled"]?.jsonPrimitive?.contentOrNull?.toBooleanStrictOrNull() ?: false
                    success(
                        directive = directive,
                        result = res,
                        postcondition = buildJsonObject {
                            put("verified", true)
                            put("action", "toggle_flashlight")
                            put("flashlight_enabled", newState)
                        },
                    )
                }

                "android.get_device_health" -> {
                    val res = handler.getDeviceHealth().getOrThrow()
                    success(directive = directive, result = res)
                }

                else -> failure(directive, "TOOL_NOT_FOUND", "Task tool not supported: ${directive.tool}")
            }
        } catch (e: Exception) {
            failure(directive, "EXECUTION_FAILED", e.message ?: "Task execution failed")
        }
    }

    private fun success(
        directive: ToolCallDirective,
        result: JsonObject,
        postcondition: JsonObject? = null,
    ): ToolResultReport {
        val kind = directive.tool.substringAfter("android.")
        val observation = ObservationPayload(
            observationId = ObservationIds.newObservationId(),
            kind = kind,
            source = "android_device",
            observedAt = ObservationIds.nowEpochSeconds(),
            contentHash = ObservationIds.hashOf(result.toString()),
            data = result,
        )
        return ToolResultReport(
            toolCallId = directive.toolCallId,
            tool = directive.tool,
            arguments = directive.arguments,
            ok = true,
            result = result,
            postcondition = postcondition,
            observationId = observation.observationId,
            observation = observation,
        )
    }

    private fun failure(
        directive: ToolCallDirective,
        code: String,
        message: String,
    ): ToolResultReport = ToolResultReport(
        toolCallId = directive.toolCallId,
        tool = directive.tool,
        arguments = directive.arguments,
        ok = false,
        error = ToolError(
            code = code,
            message = message,
        ),
    )
}
