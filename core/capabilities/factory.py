from core.capabilities.models import Capability, CapabilityState
from core.capabilities import registry, permissions, health

def register_core_capabilities(config=None):
    config = config or {}

    # System capabilities
    registry.register(Capability(capability_id="system.time", name="System Time", description="Read current system time", category="system", discovery_metadata={"tool": "current_time"}))
    registry.register(Capability(capability_id="system.info", name="System Information", description="Read OS and hardware info", category="system", discovery_metadata={"tool": "system_information"}))
    registry.register(Capability(capability_id="system.processes", name="List Processes", description="List running processes", category="system", required_permissions=["desktop.observation"], discovery_metadata={"tool": "list_processes"}))

    # Desktop/Windows capabilities
    registry.register(Capability(capability_id="desktop.windows", name="Window Management", description="List and focus windows", category="desktop", required_permissions=["desktop.observation"], discovery_metadata={"tool": "list_windows"}))
    registry.register(Capability(capability_id="desktop.input", name="Input Synthesis", description="Mouse and keyboard control", category="desktop", required_permissions=["desktop.control"], discovery_metadata={"tool": "click_mouse"}))
    registry.register(Capability(capability_id="desktop.applications", name="Open Applications", description="Launch local applications", category="desktop", required_permissions=["desktop.control"], discovery_metadata={"tool": "open_application"}))
    registry.register(Capability(capability_id="desktop.open_url", name="Open URL", description="Open a web link in the default browser", category="desktop", required_permissions=["desktop.control"], discovery_metadata={"tool": "open_url"}))
    registry.register(Capability(capability_id="desktop.commands", name="Run Commands", description="Execute local shell commands", category="desktop", required_permissions=["desktop.commands"], discovery_metadata={"tool": "run_command"}))

    # Vision capabilities
    registry.register(Capability(capability_id="vision.capture", name="Screen Capture", description="Take a screenshot", category="vision", required_permissions=["screen.capture"], discovery_metadata={"tool": "take_screenshot"}))
    registry.register(Capability(capability_id="vision.describe", name="Screen Describe", description="Ask a Vision LLM about the screen", category="vision", required_permissions=["screen.capture"], discovery_metadata={"tool": "describe_screen"}))

    # Filesystem capabilities
    registry.register(Capability(capability_id="filesystem.read", name="Read Filesystem", description="Read directories and files", category="filesystem", required_permissions=["filesystem.read"], discovery_metadata={"tool": "read_file"}))
    registry.register(Capability(capability_id="filesystem.write", name="Write Filesystem", description="Modify directories and files", category="filesystem", required_permissions=["filesystem.write"], discovery_metadata={"tool": "write_file"}))

    # Memory/Chat capabilities
    registry.register(Capability(capability_id="memory.write", name="Write Memory", description="Remember facts", category="memory", discovery_metadata={"tool": "remember"}))
    registry.register(Capability(capability_id="memory.remember", name="Remember Fact", description="Remember user facts", category="memory", discovery_metadata={"tool": "remember_fact"}))
    registry.register(Capability(capability_id="memory.forget", name="Forget Fact", description="Forget user facts", category="memory", discovery_metadata={"tool": "forget_fact"}))
    registry.register(Capability(capability_id="chat.react", name="Message Reactions", description="React to messages", category="chat", discovery_metadata={"tool": "react_to_message"}))

    # Sandbox & Tool Synthesis capabilities
    registry.register(Capability(capability_id="sandbox.execute", name="Sandbox Execution", description="Execute code in Python sandbox", category="sandbox", discovery_metadata={"tool": "python_sandbox"}))
    registry.register(Capability(capability_id="tools.synthesize", name="Tool Synthesis", description="Synthesize and register custom tools", category="tools", discovery_metadata={"tool": "create_custom_tool"}))

    # Web & Internet capabilities
    registry.register(Capability(capability_id="web.search", name="Web Search", description="Search the web for real-time information", category="web", discovery_metadata={"tool": "search_web"}))
    registry.register(Capability(capability_id="web.fetch", name="Web Content Fetch", description="Fetch and extract readable content from URL", category="web", discovery_metadata={"tool": "fetch_web_content"}))

    # Workspace & Git Pair-Programming capabilities
    registry.register(Capability(capability_id="workspace.git", name="Workspace Git", description="Inspect git repository status and code diffs", category="workspace", discovery_metadata={"tool": "workspace_git_status"}))
    registry.register(Capability(capability_id="workspace.search", name="Workspace File Search", description="Search workspace files and patterns", category="workspace", discovery_metadata={"tool": "workspace_search_files"}))

    # Canonical Android capabilities (synchronized with AndroidProvider)
    android_caps = [
        ("android.foreground_app", "Android Foreground App", "The app currently in the foreground, from accessibility metadata (package, label). Answers 'what app am I in' without any vision.", ["android.accessibility"], "android.get_foreground_app"),
        ("android.ui_tree", "Android UI Tree", "The current accessibility tree: visible nodes with id, text, bounds and clickability. This is how current UI state is read.", ["android.accessibility"], "android.get_ui_tree"),
        ("android.ui_search", "Android UI Element Search", "Find a visible node by text or content description. Returns its id so a later android.tap can target it precisely.", ["android.accessibility"], "android.find_node"),
        ("android.screen_capture", "Android Screen Capture", "Capture the current screen as an image. Visual observation; use get_foreground_app instead when only app identity is needed.", ["android.accessibility", "android.screen_capture"], "android.screenshot"),
        ("android.tap", "Android Tap", "Tap a node by id or by visible text. Prefer text when the node was just seen in a fresh tree.", ["android.accessibility"], "android.tap"),
        ("android.long_press", "Android Long Press", "Long-press a node by id or visible text.", ["android.accessibility"], "android.long_press"),
        ("android.swipe", "Android Swipe", "Swipe the screen in a direction.", ["android.accessibility"], "android.swipe"),
        ("android.text_input", "Android Text Input", "Type text into the focused field, optionally focusing one first.", ["android.accessibility"], "android.text_input"),
        ("android.key_input", "Android Key Input", "Press a key such as enter or delete.", ["android.accessibility"], "android.key_input"),
        ("android.back", "Android Back Navigation", "Press the system back button.", ["android.accessibility"], "android.back"),
        ("android.home", "Android Home Navigation", "Go to the home screen.", ["android.accessibility"], "android.home"),
        ("android.app_launch", "Android Application Launch", "Launch an app by package name. Returns while the app settles; follow with android.wait_for('foreground=<package>').", ["android.accessibility"], "android.launch_app"),
        ("android.wait_for", "Android Wait For State", "Wait until a condition holds: foreground=<package>, text_exists=<text>, node_gone=<id>, activity_changed. Bounded timeout; never a fixed sleep.", ["android.accessibility"], "android.wait_for"),
        ("android.verification", "Android State Verification", "Check a claim about current state: package_is=<pkg>, text_visible=<text>, node_exists=<id>. Verification is evidence; task completion requires it rather than the model saying complete.", ["android.accessibility"], "android.verify"),
        ("android.app_inventory", "Android App Inventory", "Enumerate the apps currently installed on the device (package, label, launchable, enabled, version). A fresh, timestamped observation; re-query for current state.", ["android.accessibility"], "android.list_apps"),
    ]

    for cap_id, name, desc, perms, tool_name in android_caps:
        registry.register(Capability(
            capability_id=cap_id,
            name=name,
            description=desc,
            category="android",
            required_permissions=perms,
            required_dependencies=["android.companion"],
            discovery_metadata={"tool": tool_name}
        ))

    # Add health checks for Android capabilities using device gateway
    def check_android_gateway():
        try:
            from server.device_gateway import get_device_gateway
            gw = get_device_gateway()
            status = gw.device_status()
            healthy = status.get("healthy", False)
            reason = status.get("reason", "")
            state = status.get("state", "UNAVAILABLE")
            return {"healthy": healthy, "reason": reason, "state": state}
        except Exception:
            return {"healthy": False, "reason": "Device gateway unavailable", "state": "UNAVAILABLE"}

    for cap_id, _, _, _, _ in android_caps:
        health.register_check(cap_id, check_android_gateway)

    # Phase 5: Android Task Capabilities (SMS, Calendar, Contacts)
    task_caps = [
        ("android.sms", "Android SMS Messaging", "Send and read SMS messages on the device.", ["android.permission.SEND_SMS", "android.permission.READ_SMS"], "android.send_sms"),
        ("android.calendar", "Android Calendar Management", "Create and query calendar events on the device.", ["android.permission.READ_CALENDAR", "android.permission.WRITE_CALENDAR"], "android.create_calendar_event"),
        ("android.contacts", "Android Contacts Search", "Search address book and device contacts.", ["android.permission.READ_CONTACTS"], "android.search_contacts"),
    ]

    for cap_id, name, desc, perms, tool_name in task_caps:
        registry.register(Capability(
            capability_id=cap_id,
            name=name,
            description=desc,
            category="android",
            required_permissions=perms,
            required_dependencies=["android.companion"],
            discovery_metadata={"tool": tool_name}
        ))
        health.register_check(cap_id, check_android_gateway)

    def check_desktop_input():
        try:
            from tools.builtins.input import default_input_synthesizer
            if default_input_synthesizer() is not None:
                return {"healthy": True, "reason": "", "state": "AVAILABLE"}
        except Exception:
            pass
        return {"healthy": False, "reason": "Input synthesizer unavailable on host", "state": "UNAVAILABLE"}

    health.register_check("desktop.input", check_desktop_input)

    def check_sandbox_available():
        return {"healthy": True, "reason": "", "state": "AVAILABLE"}

    health.register_check("sandbox.execute", check_sandbox_available)
    health.register_check("tools.synthesize", check_sandbox_available)

    def check_web_available():
        return {"healthy": True, "reason": "", "state": "AVAILABLE"}

    health.register_check("web.search", check_web_available)
    health.register_check("web.fetch", check_web_available)

    def check_workspace_available():
        return {"healthy": True, "reason": "", "state": "AVAILABLE"}

    health.register_check("workspace.git", check_workspace_available)
    health.register_check("workspace.search", check_workspace_available)

    # Permission check for android accessibility
    def check_android_accessibility():
        try:
            from server.device_gateway import get_device_gateway
            gw = get_device_gateway()
            status = gw.device_status()
            if status.get("state") in {"UNAVAILABLE", "UNKNOWN", "UNHEALTHY"}:
                return {"granted": True, "reason": ""}
            perms = status.get("permissions") or {}
            granted = perms.get("android.accessibility", True)
            if isinstance(granted, dict):
                return {"granted": bool(granted.get("granted", True)), "reason": str(granted.get("reason", ""))}
            return {"granted": bool(granted), "reason": ""}
        except Exception:
            return {"granted": True, "reason": ""}

    permissions.register_check("android.accessibility", check_android_accessibility)

    def check_android_perm(perm: str):
        def _check():
            try:
                from server.device_gateway import get_device_gateway
                gw = get_device_gateway()
                status = gw.device_status()
                if status.get("state") in {"UNAVAILABLE", "UNKNOWN", "UNHEALTHY"}:
                    return {"granted": True, "reason": ""}
                perms = status.get("permissions") or {}
                granted = perms.get(perm, True)
                if isinstance(granted, dict):
                    return {"granted": bool(granted.get("granted", True)), "reason": str(granted.get("reason", ""))}
                return {"granted": bool(granted), "reason": ""}
            except Exception:
                return {"granted": True, "reason": ""}
        return _check

    for perm in [
        "android.permission.SEND_SMS",
        "android.permission.READ_SMS",
        "android.permission.READ_CALENDAR",
        "android.permission.WRITE_CALENDAR",
        "android.permission.READ_CONTACTS",
    ]:
        permissions.register_check(perm, check_android_perm(perm))

    # Default grants for local PC permissions
    permissions.grant("system.time")
    permissions.grant("system.info")
    permissions.grant("desktop.observation")
    permissions.grant("desktop.control")
    permissions.grant("desktop.commands")
    permissions.grant("screen.capture")
    permissions.grant("filesystem.read")
    permissions.grant("filesystem.write")
    permissions.grant("memory.write")
    permissions.grant("memory.remember")
    permissions.grant("memory.forget")
    permissions.grant("chat.react")
