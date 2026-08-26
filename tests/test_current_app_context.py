"""
The CURRENT APP prompt section.

A phone that attaches its foreground-app identity to an ordinary chat
message lets Aura answer "what app am I using?" from accessibility
metadata alone. These tests pin the three behaviours that matter:

    the section renders when the context carries an app
    it stays out of prompts that carry no app (byte-identical to before)
    the device's own screen note is quoted, never re-derived

It also pins that an `app`-only context is NOT an agent tick - routing
must not change because the phone started telling the server which app is
in front of it.
"""

from brain.agent_mode import is_agent_tick
from brain.message import Message
from brain.prompt_builder import PromptBuilder


YOUTUBE = {
    "package": "com.google.android.youtube",
    "label": "YouTube",
    "activity": "com.google.android.youtube.app.hub.Shell_HomeActivity",
}


def _build(context):
    return PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="Tui đang bật app gì?"),
        context=context,
    )


class TestCurrentAppSection:
    def test_an_app_in_context_renders_the_section(self):
        prompt = _build({"app": YOUTUBE})

        assert "===== CURRENT APP =====" in prompt
        assert "Application: YouTube" in prompt
        assert "Package: com.google.android.youtube" in prompt
        assert (
            "Activity: com.google.android.youtube.app.hub.Shell_HomeActivity"
            in prompt
        )

    def test_no_app_means_no_section(self):
        # The regression guard: desktop turns and older clients must get
        # exactly the prompt they got before this section existed.
        assert "CURRENT APP" not in _build({})

        assert "CURRENT APP" not in _build({"app": {}})

        assert "CURRENT APP" not in _build({"app": {"activity": ""}})

    def test_a_label_without_a_package_still_renders(self):
        prompt = _build({"app": {"label": "Settings"}})

        assert "Application: Settings" in prompt

    def test_the_device_screen_note_is_quoted_verbatim(self):
        note = "Screenshot upload is switched off on this phone."

        prompt = _build({"app": YOUTUBE, "screen_note": f"{note}"})

        assert note in prompt

    def test_an_empty_note_adds_nothing(self):
        prompt = _build({"app": YOUTUBE, "screen_note": ""})

        lines_after_activity = prompt.split("Shell_HomeActivity", 1)[1]

        assert "switched off" not in lines_after_activity

    def test_a_non_dict_app_is_ignored_rather_than_fatal(self):
        assert "CURRENT APP" not in _build({"app": "youtube"})


class TestRouting:
    def test_app_only_context_is_not_an_agent_tick(self):
        # AGENT_TICK_KEYS are accessibility_tree and device. The chat
        # path deliberately sends neither, so a phone that names its
        # foreground app still gets a conversational reply.
        assert not is_agent_tick({"app": YOUTUBE})
        assert not is_agent_tick({"app": YOUTUBE, "screen_note": "x"})

    def test_a_real_agent_tick_is_still_one(self):
        assert is_agent_tick({"accessibility_tree": {}, "device": {}})
