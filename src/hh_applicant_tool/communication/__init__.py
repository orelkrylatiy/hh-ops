"""humanizer-framework integration surface for hh-ops.

The framework is an external pinned dependency; this package only maps local
reply state onto framework requests.
"""

from .adapter import (
    APPLICATION_BUSINESS_RULES,
    CHAT_REPLY_BUSINESS_RULES,
    DEFAULT_CHAT_VOICE,
    build_application_request,
    build_chat_reply_request,
    conversation_from_context,
)
from .live import FrameworkWriter

__all__ = [
    "APPLICATION_BUSINESS_RULES",
    "FrameworkWriter",
    "CHAT_REPLY_BUSINESS_RULES",
    "DEFAULT_CHAT_VOICE",
    "build_chat_reply_request",
    "build_application_request",
    "conversation_from_context",
]
