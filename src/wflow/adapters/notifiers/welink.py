"""WeLink notification channel — interface reserved, not yet implemented.

WeLink communicates via its own CLI tool. When implemented, this adapter
will call the `welink` CLI as a subprocess.

Config keys:
    webhook_url: str (required) — WeLink API endpoint
"""

from typing import Any

from wflow.adapters.notifiers.base import NotificationChannel


class WeLinkNotifier(NotificationChannel):
    """Sends notifications via WeLink CLI tool.

    NOT IMPLEMENTED in v1. This is an interface stub.
    """

    async def send(self, message: str, config: dict[str, Any]) -> bool:
        """Send a message to WeLink.

        Implementation sketch (for future):
            import asyncio
            url = config["webhook_url"]
            proc = await asyncio.create_subprocess_exec(
                "welink", "send", "--url", url, "--message", message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        """
        raise NotImplementedError(
            "WeLink notifier is not yet implemented. "
            "This is a reserved interface for future use."
        )
