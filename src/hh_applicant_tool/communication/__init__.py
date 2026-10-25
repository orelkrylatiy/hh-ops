"""humanizer-framework integration surface for hh-ops.

The framework is an external pinned dependency; this package only maps local
reply state onto framework requests and runs it in shadow mode.
"""

from .adapter import (
    APPLICATION_BUSINESS_RULES,
    CHAT_REPLY_BUSINESS_RULES,
    DEFAULT_CHAT_VOICE,
    ChatCompleteProvider,
    build_application_request,
    build_chat_reply_request,
    conversation_from_context,
)
from .live import FrameworkWriter
from .shadow import (
    SHADOW_ENV_FLAG,
    FrameworkShadowReplier,
    ShadowComparison,
    framework_shadow_enabled,
)

__all__ = [
    "APPLICATION_BUSINESS_RULES",
    "FrameworkWriter",
    "CHAT_REPLY_BUSINESS_RULES",
    "DEFAULT_CHAT_VOICE",
    "SHADOW_ENV_FLAG",
    "ChatCompleteProvider",
    "FrameworkShadowReplier",
    "ShadowComparison",
    "build_chat_reply_request",
    "build_application_request",
    "conversation_from_context",
    "framework_shadow_enabled",
]
