"""
The HTTP plumbing every cloud chat provider in this package repeats.

Read a key from the environment, POST JSON, turn an `HTTPError` into a
typed provider error, pull one field out of the reply. Four copies of that
existed when Phase 11 began - `groq.py`, `mistral.py`, `openrouter.py` and
the unregistered `cerebras.py` - and Phase 11 needed six more providers.
Ten copies of a rate-limit classifier is nine places for the next fix to
be missing from.

WHAT THIS IS NOT
----------------
It is not a rewrite of the working providers. `groq.py`, `mistral.py` and
`openrouter.py` are registered, tested and deliberately untouched;
OpenRouter in particular has free-model rotation that nothing else shares.
This module is what the *new* providers are built from, plus `cerebras.py`,
which could not be registered without being edited anyway (its own
docstring said why).

WHY `generate` LIVES HERE AND NOT IN THE SUBCLASSES
---------------------------------------------------
Because of the bug that kept Cerebras unwired for two phases: it sent the
whole prompt as one user message instead of calling `split_prompt`, so
Aura's instructions - including the device-action boundary from
`prompts/system.md` - arrived as ordinary conversation. That is not a
style difference, and it is the kind of mistake a copy-pasted provider
makes silently. Here, `generate` splits the prompt and a subclass only
says how to *shape* the two halves, so a new provider cannot forget.

WHAT NEVER APPEARS IN AN EXCEPTION
----------------------------------
The key. Messages are built from the provider's label and the HTTP status,
never from the request. `classify_failure` reads the response body to
classify it and puts no part of it in the error it returns, because these
errors reach a phone screen through `POST /api/providers/test`.
"""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from brain.providers.base import BaseProvider, split_prompt
from brain.providers.errors import (
    ProviderAuthError,
    ProviderParameterError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from core.logger import logger


# A 429 means one of two very different things and failover depends on the
# difference: a per-minute limit is worth trying the next provider for,
# while an exhausted account is not - `FallbackProvider` stops the chain
# on the second kind rather than burning every remaining provider.
#
# These are the substrings the existing providers already look for, plus
# the ones OpenAI and Anthropic use for a spent balance.
ACCOUNT_LIMIT_HINTS = (
    "daily",
    "rpd",
    "slow down",
    "account",
    "quota",
    "monthly",
    "insufficient_quota",
    "billing",
    "credit balance",
    "exceeded your current",
)


def provider_message(body: str) -> str:
    """
    The provider's own message, lowercased, from either error envelope.

    OpenAI-compatible and Anthropic bodies are `{"error": {"message": …}}`;
    Mistral's is `{"message": …}`. A body that is not JSON at all - an HTML
    error page from a proxy, an empty 502 - is returned as text, because
    the substring tests above still work on it and a JSON failure here
    must not become a provider failure.
    """

    try:
        data = json.loads(body)
    except Exception:
        return (body or "").lower()

    if not isinstance(data, dict):
        return (body or "").lower()

    error = data.get("error")

    if isinstance(error, dict):
        return str(error.get("message", "")).lower()

    if isinstance(error, str):
        return error.lower()

    return str(data.get("message", "")).lower()


def named_parameter(body: str, candidates) -> str:
    """
    Which of `candidates` the provider refused, or "".

    Only ever one of the optional fields the request actually sent, so a
    generic 400 stays a generic 400 and cannot be "repaired" by dropping
    something unrelated. OpenAI names the field in `error.param`; when it
    does not, the quoted name in the message is the fallback, which is how
    "Unsupported parameter: 'max_tokens' is not supported with this model"
    is understood.
    """

    if not candidates:
        return ""

    parameter = None

    try:
        data = json.loads(body)
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            parameter = error.get("param")
    except Exception:
        parameter = None

    if parameter and str(parameter) in candidates:
        return str(parameter)

    text = provider_message(body)

    for name in candidates:
        if f"'{name}'" in text or f'"{name}"' in text:
            return name

    return ""


def classify_failure(
    label: str,
    status: int,
    body: str,
    retry_after: str | None = None,
    parameters=(),
) -> Exception:
    """
    An HTTP status from a chat API, as the exception failover understands.

    The categories are the ones downstream code acts on, and no other:

        429 + account hint   ProviderRateLimitError(is_account_limit=True)
                             -> stop the chain, waiting cannot help
        429                  ProviderRateLimitError -> try the next provider
        401 / 403            ProviderAuthError -> the key is wrong
        400 naming a field   ProviderParameterError -> retry without it
        5xx / 408 / 409      ProviderUnavailableError -> transient
        anything else        RuntimeError -> unclassified, and says so

    `body` is read but never quoted: the message is the label and the
    status, because this string is rendered on a phone.
    """

    message = f"{label} HTTP {status}"

    if status == 429:

        try:
            wait = float(retry_after) if retry_after else None
        except (TypeError, ValueError):
            wait = None

        text = provider_message(body)

        return ProviderRateLimitError(
            message,
            retry_after=wait,
            is_account_limit=any(hint in text for hint in ACCOUNT_LIMIT_HINTS),
        )

    if status in (401, 403):
        return ProviderAuthError(f"{label} rejected the API key")

    if status == 400:
        refused = named_parameter(body, parameters)
        if refused:
            return ProviderParameterError(
                f"{label} does not accept {refused} for this model",
                parameter=refused,
            )

    if status >= 500 or status in (408, 409):
        return ProviderUnavailableError(message)

    return RuntimeError(message)


class HttpChatProvider(BaseProvider):
    """
    Key, endpoint, timeout, one POST, typed errors.

    A subclass declares the class attributes below and implements
    `_payload` and `_extract`. Nothing here knows any particular message
    format: `OpenAICompatibleProvider` adds the OpenAI one, and
    `AnthropicProvider` deliberately does not inherit it, because
    `/v1/messages` is a different API and a subclass that silently sent
    OpenAI JSON to it would fail in a way that looks like an outage.
    """

    # Identity. `provider_name` is the name used in config, the chain
    # label and the credential store; `label` is what a human reads in an
    # error.
    provider_name = ""
    label = ""

    # The environment variables this provider reads. `api_key_env` must
    # match `brain.router.PROVIDER_KEYS`, which is what makes a key set
    # from the phone reach it (see `core/credentials.py`).
    api_key_env = ""
    base_url_env = ""

    # The endpoint, and the path `resolve_url` will append to a root.
    default_url = ""
    endpoint_path = ""

    # Used when neither the caller nor config names a model.
    default_model = ""

    # Optional payload fields that may be dropped or renamed if the
    # provider refuses them. A required field must never be listed here -
    # Anthropic's `max_tokens` is required, so `AnthropicProvider` lists
    # only `temperature`.
    droppable = ()

    supports_text = True
    supports_vision = False

    def __init__(
        self,
        model: str | None = None,
        timeout: float = 45.0,
        max_tokens: int = 768,
        temperature: float | None = None,
    ):
        load_dotenv()

        self.api_key = (os.getenv(self.api_key_env) or "").strip()

        if not self.api_key:
            # Same shape as every other provider here, and the router's
            # `_skip_reason` names the same variable, so a missing key is
            # explained identically wherever it surfaces.
            raise ValueError(f"{self.api_key_env} is not configured")

        self.model = str(model or "").strip() or self.default_model
        self.timeout = float(timeout)
        self.max_tokens = int(max_tokens)

        # None means "do not send it", which is not the same as 0.0. A
        # provider's own default is a better answer than a number invented
        # here, and some models reject any explicit value at all.
        self.temperature = None if temperature is None else float(temperature)

        self.url = self.resolve_url(os.getenv(self.base_url_env) or "")

    # ------------------------------------------------------------------
    # Endpoint
    # ------------------------------------------------------------------

    @classmethod
    def resolve_url(cls, raw: str) -> str:
        """
        The endpoint, from either spelling of the override.

        `OPENAI_BASE_URL` is the name every OpenAI SDK uses for the API
        *root* (`https://api.openai.com/v1`), while this codebase's older
        `GROQ_BASE_URL` and `MISTRAL_BASE_URL` hold the full
        chat-completions URL. Both work here, because the failure mode for
        guessing wrong is a 404 that reads exactly like an outage.
        """

        url = (raw or "").strip().rstrip("/")

        if not url:
            return cls.default_url

        if cls.endpoint_path and not url.endswith(cls.endpoint_path):
            return url + cls.endpoint_path

        return url

    def _headers(self) -> dict:
        """Bearer auth, which is what every provider here uses but one."""

        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # The request
    # ------------------------------------------------------------------

    def _payload(self, system_instruction: str, user_content: str) -> dict:
        raise NotImplementedError

    def _extract(self, data: dict) -> str:
        raise NotImplementedError

    def _optional_parameters(self, payload: dict) -> tuple:
        """The droppable fields this request actually carries."""

        return tuple(name for name in self.droppable if name in payload)

    def _open(self, payload: dict):
        """
        The raw response, with every failure already classified.

        Returned rather than consumed so `stream` can read it line by line
        and `_post` can read it whole - one place that maps HTTP to
        exceptions, two ways of reading the body.
        """

        body = json.dumps(payload).encode("utf-8")

        request = Request(
            self.url, data=body, headers=self._headers(), method="POST"
        )

        try:
            return urlopen(request, timeout=self.timeout)

        except HTTPError as error:
            raise classify_failure(
                self.label,
                error.code,
                error.read().decode("utf-8", "replace"),
                error.headers.get("Retry-After") if error.headers else None,
                parameters=self._optional_parameters(payload),
            ) from error

        except (URLError, TimeoutError) as error:
            raise ProviderUnavailableError(f"{self.label} is unreachable") from error

    def _post(self, payload: dict) -> dict:
        """One request, one decoded JSON body."""

        try:
            with self._open(payload) as response:
                return json.loads(response.read().decode("utf-8"))

        except json.JSONDecodeError as error:
            # A 200 that is not JSON is a proxy or a captive portal
            # answering for the provider. Transient by classification so
            # the chain moves on rather than dying on it.
            raise ProviderUnavailableError(
                f"{self.label} returned a non-JSON response"
            ) from error

    def _retry_payload(self, payload: dict, parameter: str) -> dict | None:
        """
        The same request without the field the provider refused, or None.

        None means "not repairable, raise the original error". Overridden
        by `OpenAICompatibleProvider` to rename the token field rather than
        drop it, since dropping it would let a model answer at its own
        maximum length.
        """

        if parameter not in payload:
            return None

        retry = dict(payload)
        retry.pop(parameter)

        return retry

    def _send(self, payload: dict) -> dict:
        """
        Post, and repair a refused optional field exactly once.

        The retry is bounded to one attempt and to `droppable`, so this can
        never become a loop and can never quietly change what was asked
        for beyond removing a field the provider itself named.
        """

        try:
            return self._post(payload)

        except ProviderParameterError as error:

            retry = self._retry_payload(payload, error.parameter)

            if retry is None:
                raise

            logger.warning(
                "%s refused %s for model %s; retrying without it",
                self.label, error.parameter, self.model,
            )

            return self._post(retry)

    # ------------------------------------------------------------------
    # The contract
    # ------------------------------------------------------------------

    def generate(self, prompt: str) -> str:
        """
        A reply, with the prompt split into its instruction and content.

        `split_prompt` is called here rather than in each subclass on
        purpose - see the module docstring. A subclass decides what to do
        with the two halves and cannot decide not to have them.
        """

        system_instruction, user_content = split_prompt(prompt)

        data = self._send(self._payload(system_instruction, user_content))

        try:
            return self._extract(data)

        except (KeyError, IndexError, TypeError, AttributeError) as error:
            raise ProviderUnavailableError(
                f"{self.label} returned an invalid response"
            ) from error
