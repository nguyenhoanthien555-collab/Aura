"""
Per-request diagnostic traces.

One JSON line per request, in `logs/diagnostics.jsonl`, carrying the
fields the contract's observability section names: which intent, which
provider and model, how much context, what tools ran and with what
result, how the stream reconciled, and how the request ended.

Design rules the rest of the codebase already lives by:

    * Structured over prose. A logger.info line is easy to write and
      hard to query; a JSON line can be grepped, counted and graphed
      without a log parser.
    * No secrets and no user text. Values here are identifiers, counts,
      durations and enumerated reasons - the same vocabulary
      `server/errors.py` and `events/log.py` already keep to. A goal is
      truncated and optional; conversation content never enters a trace.
    * Tracing must never break the request. Every emission is wrapped;
      a diagnostics failure is logged and swallowed, because losing a
      trace line must not cost a turn.
"""

import json
import logging
import time

from core.logger import logger
from core.paths import LOGS_DIR


TRACE_FILE = LOGS_DIR / "diagnostics.jsonl"

_logger = logging.getLogger("aura.diagnostics")


def _diagnostics_logger() -> logging.Logger:
    """
    The JSONL file logger, built once.

    Separate from the main "Aura" logger on purpose: the main log is for
    people, this file is for machines. A missing logs directory is
    created rather than fatal - tracing is a passenger, not a system.
    """

    if _logger.handlers:
        return _logger

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        handler = logging.FileHandler(TRACE_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))

        _logger.addHandler(handler)
        _logger.setLevel(logging.INFO)
        _logger.propagate = False
    except OSError as error:
        logger.warning("Diagnostics trace file unavailable: %s", error)

    return _logger


def emit_trace(kind: str, **fields) -> None:
    """
    Write one trace record. Never raises.

    `kind` discriminates the boundary that wrote it ("agent_run",
    "chat_stream", ...), so one file serves every pipeline stage.
    """

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "kind": kind,
    }

    for key, value in fields.items():

        if value is None:
            continue

        record[key] = value

    try:
        _diagnostics_logger().info(json.dumps(record, default=str))
    except Exception as error:  # noqa: BLE001 - tracing must not break a request
        logger.debug("Trace emission failed: %s", error)


def provider_label(llm) -> str:
    """
    The provider name behind whatever object the caller holds.

    Callers pass adapters (RouterToolCallingLLM) or chains
    (FallbackProvider) as often as bare providers. One level of
    unwrapping, the same walk the FC adapter does, covers all three
    without each caller re-deriving it.
    """

    if llm is None:
        return ""

    direct = getattr(llm, "provider_name", None)

    if direct:
        return str(direct)

    inner = getattr(llm, "llm", None) or getattr(llm, "provider", None)

    if inner is not None:
        return str(getattr(inner, "provider_name", "") or "")

    return ""


def stream_reconciliation(produced: list[str], delivered: list[str]) -> dict:
    """
    What the stream produced versus what the transport sent.

    Fragments are counted at both boundaries of the same generator loop:
    `produced` as they leave `chat_stream`, `delivered` after each frame
    was accepted by the socket. A drop (client disconnected mid-reply, a
    frame refused) shows up as produced chars exceeding delivered chars.
    Chars, not tokens: no provider in this codebase reports token counts
    on a stream, and a character count nobody can dispute beats a token
    estimate nobody can check.
    """

    produced_chars = sum(len(fragment) for fragment in produced)
    delivered_chars = sum(len(fragment) for fragment in delivered)

    return {
        "produced_fragments": len(produced),
        "delivered_fragments": len(delivered),
        "produced_chars": produced_chars,
        "delivered_chars": delivered_chars,
        "complete": produced_chars == delivered_chars,
    }
