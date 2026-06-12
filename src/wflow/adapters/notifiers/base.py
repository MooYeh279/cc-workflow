"""Abstract notification channel interface."""

from abc import ABC, abstractmethod
from typing import Any


class NotificationChannel(ABC):
    """Abstract interface for notification channels.

    Each channel (WeLink, Slack, email, etc.) implements this interface.
    The channel is called by the engine after workflow completion or failure.
    """

    @abstractmethod
    async def send(self, message: str, config: dict[str, Any]) -> bool:
        """Send a notification message.

        Args:
            message: Rendered message text (template variables already resolved).
            config: Channel-specific configuration from workflow JSON.

        Returns:
            True if sent successfully, False otherwise.
            Non-critical failures should return False, not raise.
        """
        ...
