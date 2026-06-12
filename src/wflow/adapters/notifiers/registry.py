"""Notifier registry — manages notification channel instances."""

from typing import Any

from wflow.adapters.notifiers.base import NotificationChannel


class NotifierRegistry:
    """Registry of named notification channels.

    Usage:
        registry = NotifierRegistry()
        registry.register("welink", WeLinkNotifier())
        await registry.send("welink", "Task done!", {"webhook_url": "..."})
    """

    def __init__(self):
        self._channels: dict[str, NotificationChannel] = {}

    def register(self, name: str, channel: NotificationChannel) -> None:
        """Register a channel under a name (e.g., 'welink', 'slack')."""
        self._channels[name] = channel

    async def send(self, channel: str, message: str, config: dict[str, Any]) -> bool:
        """Send a message through the named channel.

        Returns True if sent, False if channel not found or send failed.
        """
        ch = self._channels.get(channel)
        if ch is None:
            return False
        try:
            return await ch.send(message, config)
        except Exception:
            return False

    def list_channels(self) -> list[str]:
        """Return names of all registered channels."""
        return list(self._channels.keys())
