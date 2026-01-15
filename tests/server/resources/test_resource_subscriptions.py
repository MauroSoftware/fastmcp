"""Tests for MCP resource subscriptions (ResourcesCapability subscribe=True).

Tests the full resource subscription flow including:
- Basic subscribe/unsubscribe functionality
- Manual notification triggering when data changes
- End-to-end notification receipt verification
- Automatic cleanup on client disconnect
- Resource template support
- Server capability advertisement
"""

from dataclasses import dataclass, field
from datetime import datetime

import mcp.types
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.messages import MessageHandler


@dataclass
class NotificationRecording:
    """Record of a notification that was received."""

    method: str
    notification: mcp.types.ServerNotification
    timestamp: datetime = field(default_factory=datetime.now)


class RecordingMessageHandler(MessageHandler):
    """A message handler that records all notifications."""

    def __init__(self, name: str | None = None):
        super().__init__()
        self.notifications: list[NotificationRecording] = []
        self.name = name

    async def on_notification(self, message: mcp.types.ServerNotification) -> None:
        """Record all notifications with timestamp."""
        self.notifications.append(
            NotificationRecording(method=message.root.method, notification=message)
        )

    def get_notifications(
        self, method: str | None = None
    ) -> list[NotificationRecording]:
        """Get all recorded notifications, optionally filtered by method."""
        if method is None:
            return self.notifications
        return [n for n in self.notifications if n.method == method]


@pytest.fixture
def server():
    """Create a server with test resources."""
    mcp = FastMCP(name="ResourceSubscriptionTestServer")

    # Simple resource
    @mcp.resource("resource://test/simple")
    def simple_resource() -> str:
        return "Simple resource content"

    # Dynamic resource that can change
    resource_data = {"value": "initial"}

    @mcp.resource("resource://test/dynamic")
    def dynamic_resource() -> str:
        return f"Value: {resource_data['value']}"

    # Store resource_data on server for tests to modify
    mcp._test_resource_data = resource_data  # type: ignore[attr-defined]

    # Resource template
    @mcp.resource("resource://items/{item_id}")
    def item_resource(item_id: str) -> str:
        return f"Item: {item_id}"

    return mcp


class TestResourceSubscriptionBasics:
    """Basic subscribe/unsubscribe functionality tests."""

    @pytest.mark.asyncio
    async def test_subscribe_and_unsubscribe(self, server):
        """Test basic subscribe and unsubscribe operations."""
        async with Client(server) as client:
            # Subscribe to a resource
            await client.subscribe_resource("resource://test/simple")

            # Verify client-side tracking
            assert "resource://test/simple" in client._subscribed_resources

            # Verify server-side tracking
            subscription_count = (
                await server._resource_subscription_manager.get_subscriptions_count()
            )
            assert subscription_count == 1

            # Unsubscribe
            await client.unsubscribe_resource("resource://test/simple")

            # Verify client-side tracking cleared
            assert "resource://test/simple" not in client._subscribed_resources

            # Verify server-side tracking cleared
            subscription_count = (
                await server._resource_subscription_manager.get_subscriptions_count()
            )
            assert subscription_count == 0

    @pytest.mark.asyncio
    async def test_multiple_subscriptions(self, server):
        """Test subscribing to multiple resources."""
        async with Client(server) as client:
            await client.subscribe_resource("resource://test/simple")
            await client.subscribe_resource("resource://test/dynamic")

            assert len(client._subscribed_resources) == 2
            subscription_count = (
                await server._resource_subscription_manager.get_subscriptions_count()
            )
            assert subscription_count == 2


class TestResourceNotifications:
    """Tests for manual notification triggering."""

    @pytest.mark.asyncio
    async def test_manual_notification_when_data_changes(self, server):
        """Test that manual notification works when developer triggers it."""
        handler = RecordingMessageHandler(name="test")

        async with Client(server, message_handler=handler) as client:
            # Subscribe to the dynamic resource
            await client.subscribe_resource("resource://test/dynamic")

            # Simulate data change and notify
            server._test_resource_data["value"] = "updated"  # type: ignore[attr-defined]
            await server.notify_resource_updated("resource://test/dynamic")

            # Small delay for notification propagation
            import asyncio

            await asyncio.sleep(0.05)

            # Verify notification was received
            notifications = handler.get_notifications(
                "notifications/resources/updated"
            )
            assert len(notifications) == 1

            # Verify notification content
            notification = notifications[0].notification.root
            assert isinstance(notification, mcp.types.ResourceUpdatedNotification)
            assert str(notification.params.uri) == "resource://test/dynamic"

    @pytest.mark.asyncio
    async def test_notification_not_sent_on_read(self, server):
        """Test that reading a resource does NOT trigger notifications."""
        handler = RecordingMessageHandler(name="test")

        async with Client(server, message_handler=handler) as client:
            # Subscribe to a resource
            await client.subscribe_resource("resource://test/simple")

            # Read the resource (should NOT trigger notification)
            result = await client.read_resource("resource://test/simple")
            assert result is not None

            # Small delay
            import asyncio

            await asyncio.sleep(0.05)

            # Verify no notifications were sent
            notifications = handler.get_notifications(
                "notifications/resources/updated"
            )
            assert len(notifications) == 0

    @pytest.mark.asyncio
    async def test_notification_only_to_subscribers(self, server):
        """Test that notifications only go to subscribed clients."""
        handler1 = RecordingMessageHandler(name="subscriber")
        handler2 = RecordingMessageHandler(name="non-subscriber")

        async with (
            Client(server, message_handler=handler1) as client1,
            Client(server, message_handler=handler2) as client2,
        ):
            # Only client1 subscribes
            await client1.subscribe_resource("resource://test/dynamic")

            # client2 does not subscribe but reads the resource
            _ = await client2.read_resource("resource://test/dynamic")

            # Trigger notification
            await server.notify_resource_updated("resource://test/dynamic")

            import asyncio

            await asyncio.sleep(0.05)

            # Only client1 should receive notification
            assert (
                len(handler1.get_notifications("notifications/resources/updated")) == 1
            )
            assert (
                len(handler2.get_notifications("notifications/resources/updated")) == 0
            )


class TestResourceSubscriptionCleanup:
    """Tests for automatic cleanup on disconnect."""

    @pytest.mark.asyncio
    async def test_cleanup_on_client_disconnect(self, server):
        """Test that subscriptions are cleaned up when client disconnects."""
        async with Client(server) as client:
            await client.subscribe_resource("resource://test/simple")
            await client.subscribe_resource("resource://test/dynamic")

            # Verify subscriptions exist
            subscription_count = (
                await server._resource_subscription_manager.get_subscriptions_count()
            )
            assert subscription_count == 2

        # After client disconnects, subscriptions should be cleaned up
        subscription_count = (
            await server._resource_subscription_manager.get_subscriptions_count()
        )
        assert subscription_count == 0

    @pytest.mark.asyncio
    async def test_client_tracking_cleared_on_disconnect(self, server):
        """Test that client-side subscription tracking is cleared on disconnect."""
        client = Client(server)

        async with client:
            await client.subscribe_resource("resource://test/simple")
            assert len(client._subscribed_resources) == 1

        # After disconnect, client tracking should be cleared
        assert len(client._subscribed_resources) == 0


class TestResourceTemplateSubscriptions:
    """Tests for resource template subscription support."""

    @pytest.mark.asyncio
    async def test_subscribe_to_templated_resource(self, server):
        """Test subscribing to a resource created from a template."""
        handler = RecordingMessageHandler(name="test")

        async with Client(server, message_handler=handler) as client:
            # Subscribe to a specific templated resource URI
            await client.subscribe_resource("resource://items/123")

            # Verify subscription
            assert "resource://items/123" in client._subscribed_resources

            # Trigger notification for the specific URI
            await server.notify_resource_updated("resource://items/123")

            import asyncio

            await asyncio.sleep(0.05)

            notifications = handler.get_notifications(
                "notifications/resources/updated"
            )
            assert len(notifications) == 1
            notification_root = notifications[0].notification.root
            assert isinstance(notification_root, mcp.types.ResourceUpdatedNotification)
            assert str(notification_root.params.uri) == "resource://items/123"


class TestServerCapabilities:
    """Tests for server capability advertisement."""

    @pytest.mark.asyncio
    async def test_server_advertises_subscribe_capability(self, server):
        """Test that server advertises subscribe=True in capabilities."""
        async with Client(server) as client:
            # Get server capabilities (available after initialization)
            capabilities = client.session.get_server_capabilities()

            # Verify resources capability has subscribe=True
            assert capabilities is not None
            assert capabilities.resources is not None
            assert capabilities.resources.subscribe is True
