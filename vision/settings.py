"""
Which vision model, for which processor.

Two processors read the `vision:` config section and they want different
kinds of name:

    vision/cloud_processor.py   a cloud model name  (gemini-3.6-flash)
    vision/ollama_processor.py  an Ollama tag       (qwen2.5vl:7b)

One `vision.model` key cannot be both. Whichever kind of name it held,
the other processor was misconfigured - harmless only for as long as it
was never built. `capture_screen: false` is what kept that true, which
makes it a latent bug rather than an absent one: turning screen capture
on locally handed a Gemini name to a local daemon that has no such tag.

So there are two keys, `cloud_model` and `ollama_model`, and the
resolvers below are the only readers of either. Both fall back to the
legacy `vision.model` before their own default, so a config written
before the split keeps working exactly as it did - a deployment with no
migration path for its config file (see docs/DEPLOYMENT.md) cannot
afford a key rename that silently changes which model answers.

This module imports nothing. It is read by the composition root, by the
cloud processor and by `python -m vision.debug`, and a leaf module is
what keeps that from becoming an import cycle.
"""

# Kept here rather than imported from vision.ollama_processor: that
# module needs `requests`, and the composition root asks which model to
# use before it knows whether it can build the processor at all.
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:7b"


def cloud_model(config: dict) -> str:
    """
    The model name for the cloud vision processor, or "".

    Order: `vision.cloud_model`, then the legacy `vision.model`, then
    `llm.model` - the last because a deployment that names one cloud
    model for text usually means the same family for images.

    Returns "" rather than a guess when nothing is set. The caller asks
    the provider whether it supports vision, and an empty name fails
    that check instead of sending a request to a model that cannot read
    an image.
    """

    vision = config.get("vision") or {}
    llm = config.get("llm") or {}

    return (
        vision.get("cloud_model")
        or vision.get("model")
        or llm.get("model")
        or ""
    )


def ollama_model(config: dict) -> str:
    """
    The Ollama tag for the local vision processor.

    Order: `vision.ollama_model`, then the legacy `vision.model`, then
    the default tag.

    The legacy step is why a stale config still resolves to *something*
    here: on a machine whose `vision.model` is a cloud name this returns
    that cloud name, Ollama answers 404, and the manager degrades to
    window titles with a warning - the same behaviour as before the
    split. Setting `ollama_model` is what fixes it, and is now possible
    without breaking the server.
    """

    vision = config.get("vision") or {}

    return (
        vision.get("ollama_model")
        or vision.get("model")
        or DEFAULT_OLLAMA_MODEL
    )
