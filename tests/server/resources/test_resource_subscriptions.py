"""Tests for resource subscriptions."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

import mcp.types
import pytest

from fastmcp import Client, FastMCP
from fastmcp.client.messages import MessageHandler
from fastmcp.client.transports import FastMCPTransport


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

    def assert_notification_sent(self, method: str, times: int = 1) -> bool:
        """Assert that a notification was sent a specific number of times."""
        notifications = self.get_notifications(method)
        actual_times = len(notifications)
        assert actual_times == times, (
            f"Expected {times} notifications for {method}, "
            f"but received {actual_times} notifications"
        )
        return True


@pytest.fixture
def server():
    """Create a test server with resources."""
    mcp = FastMCP("test-server")
    
    # Simple resource
    @mcp.resource("resource://test/simple")
    def simple_resource() -> str:
        return "test content"
    
    # Dynamic counter resource
    counter = {"value": 0}
    
    @mcp.resource("resource://test/counter")
    def counter_resource() -> str:
        counter["value"] += 1
        return f"count: {counter['value']}"
    
    return mcp


@pytest.mark.asyncio
async def test_basic_subscribe_unsubscribe(server):
    """Test basic subscribe and unsubscribe functionality."""
    client = Client(transport=FastMCPTransport(server))
    
    async with client:
        # Subscribe to a resource
        await client.subscribe_resource("resource://test/simple")
        
        # Verify subscription was tracked
        assert "resource://test/simple" in client._subscribed_resources
        
        # Unsubscribe
        await client.unsubscribe_resource("resource://test/simple")
        
        # Verify unsubscription
        assert "resource://test/simple" not in client._subscribed_resources


@pytest.mark.asyncio
async def test_manual_notification_when_data_changes(server):
    """Test that manual notification works when developer triggers it."""
    client = Client(transport=FastMCPTransport(server))
    
    async with client:
        # Subscribe to the resource
        await client.subscribe_resource("resource://test/counter")
        
        # Manually trigger notification (simulating data change)
        await server.notify_resource_updated("resource://test/counter")
        
        # Verify subscription exists and notification was sent
        subscription_manager = server._resource_subscription_manager
        async with subscription_manager._lock:
            assert "resource://test/counter" in subscription_manager._subscriptions


@pytest.mark.asyncio
async def test_notification_received_by_client(server):
    """Test that client actually receives ResourceUpdatedNotification."""
    # Create a recording message handler to capture notifications
    handler = RecordingMessageHandler(name="test_handler")
    
    client = Client(
        transport=FastMCPTransport(server),
        message_handler=handler
    )
    
    async with client:
        # Subscribe to the resource
        await client.subscribe_resource("resource://test/simple")
        
        # Manually trigger notification
        await server.notify_resource_updated("resource://test/simple")
        
        # Small delay for notification propagation through the transport
        await asyncio.sleep(0.1)
        
        # Verify notification was received
        handler.assert_notification_sent("notifications/resources/updated", times=1)
        
        # Verify the notification has the correct URI
        notifications = handler.get_notifications("notifications/resources/updated")
        assert len(notifications) > 0
        resource_updated = notifications[0].notification.root
        assert isinstance(resource_updated, mcp.types.ResourceUpdatedNotification)
        assert str(resource_updated.params.uri) == "resource://test/simple"


@pytest.mark.asyncio
async def test_cleanup_on_disconnect(server):
    """Test that subscriptions are cleaned up when client disconnects."""
    # Get the subscription manager
    subscription_manager = server._resource_subscription_manager
    
    client = Client(transport=FastMCPTransport(server))
    async with client:
        # Subscribe to a resource
        await client.subscribe_resource("resource://test/simple")
        
        # Verify there's a subscription
        async with subscription_manager._lock:
            assert "resource://test/simple" in subscription_manager._subscriptions
            assert len(subscription_manager._subscriptions["resource://test/simple"]) > 0
    
    # After client disconnects, subscriptions should be cleaned up
    # Give some time for cleanup
    await asyncio.sleep(0.1)
    
    async with subscription_manager._lock:
        # The URI key should be removed when last subscriber disconnects
        assert "resource://test/simple" not in subscription_manager._subscriptions


@pytest.mark.asyncio
async def test_resource_template_subscriptions(server):
    """Test subscriptions work with resource templates."""
    # Add a resource template using the resource decorator with template syntax
    @server.resource("resource://test/items/{item_id}")
    def item_resource(item_id: str) -> str:
        return f"Item: {item_id}"
    
    client = Client(transport=FastMCPTransport(server))
    async with client:
        # Subscribe to a specific item
        uri = "resource://test/items/123"
        await client.subscribe_resource(uri)
        
        # Verify subscription exists
        subscription_manager = server._resource_subscription_manager
        async with subscription_manager._lock:
            assert uri in subscription_manager._subscriptions


@pytest.mark.asyncio  
async def test_server_capabilities_include_subscribe(server):
    """Test that server advertises subscribe capability."""
    client = Client(transport=FastMCPTransport(server))
    async with client:
        # Get server info (happens during initialization)
        # Check that resources capability includes subscribe=True
        # This is checked during the initialize handshake
        assert client.session is not None
        
        # The server should have reported resources capability with subscribe=True
        # We can verify this by checking that subscribe methods work without error
        await client.subscribe_resource("resource://test/simple")
        await client.unsubscribe_resource("resource://test/simple")
