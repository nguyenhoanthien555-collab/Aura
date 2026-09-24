"""
Which vision model for the cloud vision processor.

`vision/cloud_processor.py` reads the `vision:` config section and wants
a cloud model name (e.g. gemini-3.6-flash). The resolver below is the
only reader, and it falls back to the legacy `vision.model` before an
empty string, so a config written before the `cloud_model` key existed
keeps working - a deployment with no migration path for its config file
(see docs/DEPLOYMENT.md) cannot afford a key rename that silently
changes which model answers.

This module imports nothing. It is read by the composition root and by
the cloud processor, and a leaf module is what keeps that from becoming
an import cycle.
"""


def cloud_model(config: dict) -> str:
    """
    The model name for the cloud vision processor, or "".

    Order: `vision.cloud_model`, then the legacy `vision.model`.

    Returns "" rather than a guess when nothing is set. The caller asks
    the provider whether it supports vision, and an empty name fails
    that check instead of sending a request to a model that cannot read
    an image.
    """

    vision = config.get("vision") or {}

    return (
        vision.get("cloud_model")
        or vision.get("model")
        or ""
    )
