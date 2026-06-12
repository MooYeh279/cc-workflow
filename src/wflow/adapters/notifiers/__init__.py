"""Notification channel adapters.

Channels:
    welink — WeLink CLI integration (interface reserved, not yet implemented)

Future channels (Slack, DingTalk, email) should follow the same pattern:
    1. Implement NotificationChannel ABC
    2. Register with NotifierRegistry by name
"""

from wflow.adapters.notifiers.base import NotificationChannel
from wflow.adapters.notifiers.registry import NotifierRegistry
from wflow.adapters.notifiers.welink import WeLinkNotifier

__all__ = ["NotificationChannel", "NotifierRegistry", "WeLinkNotifier"]
