from core.capabilities.models import Capability, CapabilityState
from core.capabilities import registry, permissions, health

def register_core_capabilities(config=None):
    config = config or {}

    # System capabilities
    registry.register(Capability(capability_id="system.time", name="System Time", description="Read current system time", category="system"))
    registry.register(Capability(capability_id="system.info", name="System Information", description="Read OS and hardware info", category="system"))
    registry.register(Capability(capability_id="system.processes", name="List Processes", description="List running processes", category="system", required_permissions=["desktop.observation"]))

    # Desktop/Windows capabilities
    registry.register(Capability(capability_id="desktop.windows", name="Window Management", description="List and focus windows", category="desktop", required_permissions=["desktop.observation"]))
    registry.register(Capability(capability_id="desktop.input", name="Input Synthesis", description="Mouse and keyboard control", category="desktop", required_permissions=["desktop.control"]))
    registry.register(Capability(capability_id="desktop.applications", name="Open Applications", description="Launch local applications", category="desktop", required_permissions=["desktop.control"]))
    registry.register(Capability(capability_id="desktop.commands", name="Run Commands", description="Execute local shell commands", category="desktop", required_permissions=["desktop.commands"]))

    # Vision capabilities
    registry.register(Capability(capability_id="vision.capture", name="Screen Capture", description="Take a screenshot", category="vision", required_permissions=["screen.capture"]))
    registry.register(Capability(capability_id="vision.describe", name="Screen Describe", description="Ask a Vision LLM about the screen", category="vision", required_permissions=["screen.capture"]))

    # Filesystem capabilities
    registry.register(Capability(capability_id="filesystem.read", name="Read Filesystem", description="Read directories and files", category="filesystem", required_permissions=["filesystem.read"]))
    registry.register(Capability(capability_id="filesystem.write", name="Write Filesystem", description="Modify directories and files", category="filesystem", required_permissions=["filesystem.write"]))

    # Memory/Chat capabilities
    registry.register(Capability(capability_id="memory.write", name="Write Memory", description="Remember facts", category="memory"))
    registry.register(Capability(capability_id="chat.react", name="Message Reactions", description="React to messages", category="chat"))

    # Android capabilities
    registry.register(Capability(capability_id="android.observation", name="Android Observation", description="Read Android UI state, foreground app, and screen", category="android", required_permissions=["android.accessibility"]))
    registry.register(Capability(capability_id="android.control", name="Android Control", description="Tap, swipe, and type on the Android device", category="android", required_permissions=["android.accessibility"]))
    registry.register(Capability(capability_id="android.launch", name="Android App Launch", description="Launch Android applications", category="android", required_permissions=["android.accessibility"]))

    # Add health checks for Android capabilities using device gateway
    def check_android_gateway():
        try:
            from server.device_gateway import get_device_gateway
            gw = get_device_gateway()
            if not gw.is_connected():
                return {"healthy": False, "reason": "No companion poll heartbeat detected"}
            return {"healthy": True, "reason": ""}
        except Exception:
            return {"healthy": False, "reason": "Device gateway unavailable"}
            
    health.register_check("android.observation", check_android_gateway)
    health.register_check("android.control", check_android_gateway)
    health.register_check("android.launch", check_android_gateway)

    # We mock or set some permissions to True since in this environment they are currently unchecked/granted implicitly.
    permissions.grant("system.time")
    permissions.grant("system.info")
    permissions.grant("desktop.observation")
    permissions.grant("desktop.control")
    permissions.grant("desktop.commands")
    permissions.grant("screen.capture")
    permissions.grant("filesystem.read")
    permissions.grant("filesystem.write")
    permissions.grant("memory.write")
    permissions.grant("chat.react")
    permissions.grant("android.accessibility")

