"""
Android Personal Task Tools Provider (Phase 5).

Provides high-level personal task management tools over the Android DeviceBridge:
    * SMS: android.send_sms (DANGEROUS, requires confirmation), android.read_sms (SAFE, READ_ONLY)
    * Calendar: android.create_calendar_event (DANGEROUS, requires confirmation), android.list_calendar_events (SAFE, READ_ONLY)
    * Contacts: android.search_contacts (SAFE, READ_ONLY)

All mutating tools produce verified postcondition evidence upon successful execution.
All read tools produce observation evidence.
Gated behind permission checks (SEND_SMS, READ_SMS, READ_CALENDAR, WRITE_CALENDAR, READ_CONTACTS).
"""

from core.logger import logger
from core.capabilities import health, permissions, registry as capability_registry
from core.capabilities.models import Capability
from tools.base import Parameter, ToolResult, ToolRisk
from tools.outcome import SideEffect, ToolStatus
from tools.providers.android_bridge import DeviceBridge
from tools.providers.android_provider import _Read, _Mutation, tool_result_from_report
from tools.providers.base import CapabilityProvider
from tools.registry import ToolRegistry


class SendSMS(_Mutation):
    name = "android.send_sms"
    capability = "android.sms"
    description = (
        "Send an SMS text message to a specified recipient phone number. "
        "Mutating action requiring explicit operator confirmation."
    )
    parameters = (
        Parameter(
            name="recipient",
            type="string",
            description="Recipient phone number or contact identifier.",
        ),
        Parameter(
            name="message",
            type="string",
            description="Text body of the SMS message to send.",
        ),
    )


class ReadSMS(_Read):
    name = "android.read_sms"
    capability = "android.sms"
    description = (
        "Read recent SMS text messages from the device inbox, "
        "optionally filtered by a search query or sender number."
    )
    parameters = (
        Parameter(
            name="limit",
            type="integer",
            required=False,
            description="Maximum number of messages to retrieve.",
        ),
        Parameter(
            name="query",
            type="string",
            required=False,
            description="Optional search query to filter messages by text or sender.",
        ),
    )


class CreateCalendarEvent(_Mutation):
    name = "android.create_calendar_event"
    capability = "android.calendar"
    description = (
        "Schedule and create a new event in the device calendar. "
        "Mutating action requiring explicit operator confirmation."
    )
    parameters = (
        Parameter(
            name="title",
            type="string",
            description="Title or summary of the calendar event.",
        ),
        Parameter(
            name="start_time",
            type="string",
            description="Start time of the event (ISO 8601 string or YYYY-MM-DDTHH:MM:SS).",
        ),
        Parameter(
            name="end_time",
            type="string",
            required=False,
            description="Optional end time of the event.",
        ),
        Parameter(
            name="description",
            type="string",
            required=False,
            description="Optional detailed description or location notes for the event.",
        ),
    )


class ListCalendarEvents(_Read):
    name = "android.list_calendar_events"
    capability = "android.calendar"
    description = (
        "List scheduled calendar events from the device, "
        "optionally starting from a specified date."
    )
    parameters = (
        Parameter(
            name="start_date",
            type="string",
            required=False,
            description="Optional start date/time filter (ISO 8601 string or YYYY-MM-DD).",
        ),
        Parameter(
            name="limit",
            type="integer",
            required=False,
            description="Maximum number of events to list.",
        ),
    )


class SearchContacts(_Read):
    name = "android.search_contacts"
    capability = "android.contacts"
    description = (
        "Search device contacts and address book by name, phone number, or email address."
    )
    parameters = (
        Parameter(
            name="query",
            type="string",
            description="Contact name, phone number, or email query string.",
        ),
        Parameter(
            name="limit",
            type="integer",
            required=False,
            description="Maximum number of matching contacts to return.",
        ),
    )


class SetAlarm(_Mutation):
    name = "android.set_alarm"
    capability = "android.alarm"
    risk = ToolRisk.SAFE
    description = (
        "Set or schedule an alarm on the user's Android phone with hour, minute, and optional label. "
        "Supports optional repeat days of the week."
    )
    parameters = (
        Parameter(
            name="hour",
            type="integer",
            description="Hour of the day to trigger alarm (0-23 in 24-hour format).",
        ),
        Parameter(
            name="minute",
            type="integer",
            description="Minute of the hour to trigger alarm (0-59).",
        ),
        Parameter(
            name="label",
            type="string",
            required=False,
            description="Optional label or purpose of the alarm (e.g. 'Dậy đi làm', 'Tập thể dục').",
        ),
        Parameter(
            name="repeat_days",
            type="array",
            required=False,
            description="Optional list of integers representing days of week (1=Mon .. 7=Sun). Empty for one-shot alarm.",
        ),
    )


class ListAlarms(_Read):
    name = "android.list_alarms"
    capability = "android.alarm"
    description = (
        "List all alarms currently configured on the user's Android phone."
    )
    parameters = ()


class CancelAlarm(_Mutation):
    name = "android.cancel_alarm"
    capability = "android.alarm"
    risk = ToolRisk.SAFE
    description = (
        "Cancel or remove a scheduled alarm by its unique alarm ID."
    )
    parameters = (
        Parameter(
            name="alarm_id",
            type="string",
            description="Unique identifier of the alarm to cancel.",
        ),
    )


class AndroidTaskProvider(CapabilityProvider):
    """
    Capability provider for Android personal task management tools
    (SMS, Calendar, Contacts, Alarms).
    """

    namespace = "android"

    TOOLS = (
        SendSMS,
        ReadSMS,
        CreateCalendarEvent,
        ListCalendarEvents,
        SearchContacts,
        SetAlarm,
        ListAlarms,
        CancelAlarm,
    )

    _CAPABILITY_NAMES = {
        "android.sms": "Android SMS Messaging",
        "android.calendar": "Android Calendar Management",
        "android.contacts": "Android Contacts Search",
        "android.alarm": "Android Cyber Alarm System",
    }

    _CAPABILITY_KEYWORDS = {
        "android.sms": ["sms", "text", "message", "send", "inbox", "tin nhắn"],
        "android.calendar": ["calendar", "event", "schedule", "meeting", "reminder", "lịch", "cuộc hẹn"],
        "android.contacts": ["contact", "person", "phone", "number", "email", "address", "danh bạ"],
        "android.alarm": ["alarm", "wake", "wake up", "timer", "báo thức", "đánh thức", "gọi dậy", "hẹn giờ"],
    }

    _CAPABILITY_PERMISSIONS = {
        "android.sms": ["android.permission.SEND_SMS", "android.permission.READ_SMS"],
        "android.calendar": ["android.permission.READ_CALENDAR", "android.permission.WRITE_CALENDAR"],
        "android.contacts": ["android.permission.READ_CONTACTS"],
        "android.alarm": [],
    }

    def __init__(self, bridge: DeviceBridge | None = None):
        self.bridge = bridge

    def _status(self) -> dict:
        if self.bridge is None:
            return {
                "state": "UNAVAILABLE",
                "healthy": False,
                "reason": "no Android bridge is connected",
                "permissions": {},
            }
        checker = getattr(self.bridge, "status", None)
        if not callable(checker):
            return {
                "state": "UNKNOWN",
                "healthy": False,
                "reason": "Android bridge exposes no runtime status",
                "permissions": {},
            }
        try:
            result = checker()
        except Exception as error:
            return {
                "state": "UNHEALTHY",
                "healthy": False,
                "reason": f"Android bridge status failed: {type(error).__name__}",
                "permissions": {},
            }
        return result if isinstance(result, dict) else {
            "state": "UNKNOWN",
            "healthy": False,
            "reason": "Android bridge returned malformed runtime status",
            "permissions": {},
        }

    def _permission_check(self, permission: str) -> dict:
        status = self._status()
        if status.get("state") in {"UNAVAILABLE", "UNKNOWN", "UNHEALTHY"}:
            return {"granted": True, "reason": ""}
        permissions_map = status.get("permissions") or {}
        granted = permissions_map.get(permission, False)
        if isinstance(granted, dict):
            return {
                "granted": bool(granted.get("granted", False)),
                "reason": str(granted.get("reason", "")),
            }
        if granted:
            return {"granted": True, "reason": ""}
        return {
            "granted": False,
            "reason": str(status.get("permission_reasons", {}).get(
                permission,
                status.get("reason", f"{permission} was not granted on device"),
            )),
        }

    def _health_check(self, capability_id: str) -> dict:
        status = self._status()
        reported_capabilities = status.get("capabilities")
        capability_status = (reported_capabilities or {}).get(capability_id)
        if isinstance(capability_status, dict):
            return {
                "healthy": bool(capability_status.get("healthy", status.get("healthy", False))),
                "state": capability_status.get("state", status.get("state", "UNKNOWN")),
                "reason": capability_status.get("reason", status.get("reason", "")),
            }
        return {
            "healthy": bool(status.get("healthy", False)),
            "state": status.get("state", "UNKNOWN"),
            "reason": status.get("reason", "Android runtime did not report a reason"),
        }

    def _register_capabilities(self) -> None:
        """Mirror task capabilities into the authoritative capability registry."""
        for tool_cls in self.TOOLS:
            cap_id = tool_cls.capability
            required = self._CAPABILITY_PERMISSIONS.get(cap_id, [])
            cap = Capability(
                capability_id=cap_id,
                name=self._CAPABILITY_NAMES.get(cap_id, cap_id),
                description=tool_cls.description,
                category="android",
                required_permissions=required,
                required_dependencies=["android.companion"],
                availability_state="UNKNOWN",
                discovery_metadata={
                    "implemented": True,
                    "source": "AndroidTaskProvider",
                    "tool": tool_cls.name,
                    "keywords": self._CAPABILITY_KEYWORDS.get(cap_id, []),
                },
            )
            capability_registry.register(cap)
            for permission in required:
                permissions.register_check(
                    permission,
                    lambda permission=permission: self._permission_check(permission),
                )
            health.register_check(
                cap_id,
                lambda cap_id=cap_id: self._health_check(cap_id),
            )

    def available(self) -> bool:
        return self.bridge is not None

    def capabilities(self) -> list:
        if self.bridge is None:
            return []

        self._register_capabilities()
        return [tool_cls(self.bridge) for tool_cls in self.TOOLS]

    def register_into(self, registry: ToolRegistry) -> int:
        if not self.available():
            logger.info("AndroidTaskProvider has no device bridge; registering nothing")
            return 0

        return super().register_into(registry)


__all__ = [
    "AndroidTaskProvider",
    "SendSMS",
    "ReadSMS",
    "CreateCalendarEvent",
    "ListCalendarEvents",
    "SearchContacts",
    "SetAlarm",
    "ListAlarms",
    "CancelAlarm",
]

