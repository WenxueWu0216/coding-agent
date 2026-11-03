"""Anthropic client for the eval harness and workflow repair.

ANTHROPIC_API_KEY is reserved in some environments and not passed to programs, so the eval reads
EVAL_ANTHROPIC_API_KEY when it is set. With that key the client talks to https://api.anthropic.com
(or EVAL_ANTHROPIC_BASE_URL), so a container-level ANTHROPIC_BASE_URL cannot send the key elsewhere.
Without it, the SDK's defaults apply (ANTHROPIC_API_KEY, ANTHROPIC_BASE_URL).
The key is never printed, logged or written anywhere.
"""
import os

import anthropic

REAL_API = "https://api.anthropic.com"


def make_client(**kwargs) -> anthropic.Anthropic:
    key = os.environ.get("EVAL_ANTHROPIC_API_KEY")
    if key:
        return anthropic.Anthropic(api_key=key, base_url=os.environ.get("EVAL_ANTHROPIC_BASE_URL", REAL_API), **kwargs)
    return anthropic.Anthropic(**kwargs)


def has_key() -> bool:
    return bool(os.environ.get("EVAL_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
