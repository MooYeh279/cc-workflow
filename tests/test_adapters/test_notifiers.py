import pytest
from wflow.adapters.notifiers.base import NotificationChannel
from wflow.adapters.notifiers.registry import NotifierRegistry
from wflow.adapters.notifiers.welink import WeLinkNotifier


class MockChannel(NotificationChannel):
    def __init__(self):
        self.messages = []

    async def send(self, message: str, config: dict) -> bool:
        self.messages.append((message, config))
        return True


class FailingChannel(NotificationChannel):
    async def send(self, message: str, config: dict) -> bool:
        return False


@pytest.mark.asyncio
async def test_registry_send_to_registered_channel():
    registry = NotifierRegistry()
    mock = MockChannel()
    registry.register("test", mock)
    result = await registry.send("test", "hello", {"key": "value"})
    assert result is True
    assert mock.messages == [("hello", {"key": "value"})]


@pytest.mark.asyncio
async def test_registry_send_to_unknown_channel():
    registry = NotifierRegistry()
    result = await registry.send("nonexistent", "hello", {})
    assert result is False


@pytest.mark.asyncio
async def test_registry_list_channels():
    registry = NotifierRegistry()
    registry.register("a", MockChannel())
    registry.register("b", MockChannel())
    assert set(registry.list_channels()) == {"a", "b"}


@pytest.mark.asyncio
async def test_welink_not_implemented():
    welink = WeLinkNotifier()
    with pytest.raises(NotImplementedError):
        await welink.send("test", {"webhook_url": "..."})


def test_notification_channel_is_abstract():
    with pytest.raises(TypeError):
        NotificationChannel()
