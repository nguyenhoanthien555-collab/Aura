"""
Capability routing: one AURA, many interchangeable workers.

Two things are being pinned here, and they pull in opposite directions.

The first is that a task class may pick a different model. A coding
question and a one-word intent probe have no business going to the same
worker when the owner has configured better choices for each.

The second is that nothing above the router may notice. The prompt bytes,
the persona, the transcript and the owner's configured provider setting
must all be identical no matter which lane answered. A router that
changes what AURA is, in order to change which model answers, has broken
the only rule that matters.

So most of this file is about what must NOT change.
"""

import pytest

from brain.capabilities import TaskClass, classify_task, generate_for
from brain.model_router import CapabilityRouter


class Recorder:
    """An LLM that remembers exactly what it was asked."""

    def __init__(self, name: str, reply: str = "ok"):
        self.provider_name = name
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


class Dead:
    """A provider that cannot answer."""

    provider_name = "dead"

    def generate(self, prompt: str) -> str:
        raise RuntimeError("provider is down")


class TestTaskClasses:

    def test_every_mandated_task_class_exists(self):
        for name in (
            "CHAT", "REASONING", "CODING", "VISION", "TOOL_PLANNING",
            "FAST_RESPONSE", "LONG_CONTEXT", "EMBEDDING", "FALLBACK",
        ):
            assert hasattr(TaskClass, name), name

    def test_a_task_class_is_its_own_string(self):
        # Lane configuration arrives as JSON from a phone, so the enum has
        # to compare equal to the string that was sent.
        assert TaskClass.CODING == "coding"


class TestClassification:

    def test_an_agent_tick_is_tool_planning(self):
        task = classify_task("agent_tick", context={"accessibility_tree": "..."})
        assert task is TaskClass.TOOL_PLANNING

    def test_a_device_snapshot_alone_is_also_tool_planning(self):
        task = classify_task("agent_tick", context={"device": {"foreground": "com.x"}})
        assert task is TaskClass.TOOL_PLANNING

    def test_an_intent_probe_wants_the_fastest_worker(self):
        task = classify_task("mo youtube", context={"intent_probe": True})
        assert task is TaskClass.FAST_RESPONSE

    def test_a_coding_question_is_coding(self):
        assert classify_task("sua cai function nay trong python") is TaskClass.CODING

    def test_a_broken_thing_is_reasoning(self):
        assert classify_task("no crash roi, traceback dai lam") is TaskClass.REASONING

    def test_plain_conversation_is_chat(self):
        assert classify_task("hom nay cau the nao") is TaskClass.CHAT

    def test_nothing_is_chat(self):
        assert classify_task("") is TaskClass.CHAT
        assert classify_task(None) is TaskClass.CHAT

    def test_a_very_long_turn_is_long_context(self):
        history = ["x" * 4000, "y" * 4000]
        assert classify_task("tom tat di", history=history) is TaskClass.LONG_CONTEXT

    def test_a_machine_turn_outranks_length(self):
        # An agent tick carrying a huge accessibility tree is still an
        # agent tick. Routing it to a long-context chat model would send a
        # JSON-action request to a worker chosen for prose.
        task = classify_task(
            "agent_tick",
            context={"accessibility_tree": "n" * 20000},
            history=["z" * 9000],
        )
        assert task is TaskClass.TOOL_PLANNING

    def test_classification_reads_nothing_but_its_arguments(self):
        # Pure: the same inputs classify the same way regardless of
        # configuration, environment or call order.
        assert classify_task("build gradle loi") is classify_task("build gradle loi")


class TestGenerateForDegrades:

    def test_a_plain_llm_is_still_usable(self):
        # Every existing provider, mock and test fake has only `generate`.
        # None of them may need editing.
        llm = Recorder("plain")
        assert generate_for(llm, "p", TaskClass.CODING) == "ok"
        assert llm.prompts == ["p"]

    def test_a_capability_aware_llm_receives_the_task(self):
        seen = {}

        class Aware:
            def generate(self, prompt):
                seen["plain"] = True
                return "wrong"

            def generate_for(self, prompt, task):
                seen["task"] = task
                return "right"

        assert generate_for(Aware(), "p", TaskClass.REASONING) == "right"
        assert seen["task"] is TaskClass.REASONING
        assert "plain" not in seen

    def test_no_task_means_the_plain_path(self):
        llm = Recorder("plain")
        assert generate_for(llm, "p", None) == "ok"


class TestCapabilityRouter:

    def test_chat_is_the_default_lane(self):
        chat = Recorder("chat")
        router = CapabilityRouter(chat=chat)
        assert router.generate("p") == "ok"
        assert chat.prompts == ["p"]

    def test_a_configured_lane_answers_its_own_tasks(self):
        chat, coder = Recorder("chat", "prose"), Recorder("coder", "code")
        router = CapabilityRouter(chat=chat, lanes={TaskClass.CODING: coder})

        assert router.generate_for("p", TaskClass.CODING) == "code"
        assert router.generate_for("p", TaskClass.CHAT) == "prose"
        assert coder.prompts == ["p"]
        assert chat.prompts == ["p"]

    def test_an_unconfigured_lane_uses_chat(self):
        chat = Recorder("chat", "prose")
        router = CapabilityRouter(chat=chat)
        assert router.generate_for("p", TaskClass.REASONING) == "prose"

    def test_a_dead_lane_degrades_to_chat_instead_of_failing(self):
        # Provider isolation, applied to lanes: a broken optional lane
        # must not take AURA down.
        chat = Recorder("chat", "prose")
        router = CapabilityRouter(chat=chat, lanes={TaskClass.CODING: Dead()})
        assert router.generate_for("p", TaskClass.CODING) == "prose"

    def test_a_dead_chat_lane_still_raises(self):
        # Nothing left to degrade to. Silently returning "" here would
        # look to the user like AURA had nothing to say.
        router = CapabilityRouter(chat=Dead())
        with pytest.raises(RuntimeError):
            router.generate("p")

    def test_the_prompt_is_byte_identical_across_lanes(self):
        # The whole point. Two different workers, one AURA.
        chat, coder = Recorder("chat"), Recorder("coder")
        router = CapabilityRouter(chat=chat, lanes={TaskClass.CODING: coder})
        prompt = "===== PERSONA =====\nto la Aura\n\n===== USER =====\nhi"

        router.generate_for(prompt, TaskClass.CHAT)
        router.generate_for(prompt, TaskClass.CODING)

        assert chat.prompts == [prompt]
        assert coder.prompts == [prompt]

    def test_it_satisfies_the_llm_port(self):
        from brain.ports import LLM

        assert isinstance(CapabilityRouter(chat=Recorder("c")), LLM)

    def test_provider_name_is_readable_and_writable(self):
        # `server/settings_service.py` writes this when the owner changes
        # provider. Losing it would break that path.
        router = CapabilityRouter(chat=Recorder("chat"))
        assert router.provider_name == "chat"
        router.provider_name = "groq"
        assert router.provider_name == "groq"

    def test_invalidating_the_provider_is_honoured(self):
        router = CapabilityRouter(chat=Recorder("chat"))
        router._provider = None
        assert router._provider is None

    def test_active_chain_reports_what_answers(self):
        assert "chat" in CapabilityRouter(chat=Recorder("chat")).active_chain()

    def test_lanes_are_reported_for_diagnostics(self):
        coder = Recorder("coder")
        router = CapabilityRouter(chat=Recorder("chat"), lanes={TaskClass.CODING: coder})
        described = router.describe_lanes()
        assert described[TaskClass.CHAT] == "chat"
        assert described[TaskClass.CODING] == "coder"

    def test_streaming_is_offered_only_when_the_chat_lane_streams(self):
        from brain.streaming import can_stream

        assert not can_stream(CapabilityRouter(chat=Recorder("chat")))

        class Streamer(Recorder):
            def stream(self, prompt):
                yield "a"
                yield "b"

        router = CapabilityRouter(chat=Streamer("s"))
        assert can_stream(router)
        assert "".join(router.stream("p")) == "ab"


class TestOwnerConfigurationIsNeverRewritten:

    def test_routing_does_not_touch_the_provider_setting(self):
        from core import settings_store

        chat, coder = Recorder("chat"), Recorder("coder")
        router = CapabilityRouter(chat=chat, lanes={TaskClass.CODING: coder})

        router.generate_for("p", TaskClass.CODING)
        router.generate_for("p", TaskClass.REASONING)

        overlay = settings_store.get_runtime_settings().overrides
        assert "llm.provider" not in overlay
        assert not any(key.startswith("llm.task_models") for key in overlay)


class _Row:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class _ThrowawayStore:
    """A ConversationStore that keeps turns in a list."""

    def __init__(self):
        self.rows = []

    def save(self, role, content, session_id="default"):
        self.rows.append(_Row(role, content))

    def get_recent(self, limit=10, session_id="default"):
        return list(reversed(self.rows))[:limit]


class TestPersonalityDoesNotDriftAcrossModels:

    def test_the_same_conversation_yields_the_same_prompt_on_every_model(self):
        """
        Section 39: do not rely on model compliance.

        Two adapters that reply completely differently must still have
        been asked the same thing, with the same identity in it.
        """
        from brain.chat_engine import ChatEngine

        prompts: dict[str, str] = {}

        for name, reply in (("a", "xin chao"), ("b", "hello there")):
            llm = Recorder(name, reply)
            engine = ChatEngine(llm=llm, memory=_ThrowawayStore())
            engine.chat("cau ten gi")
            prompts[name] = llm.prompts[0]

        assert prompts["a"] == prompts["b"]
        assert prompts["a"].strip()
