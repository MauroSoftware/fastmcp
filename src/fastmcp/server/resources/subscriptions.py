"""Resource subscription manager for MCP resource subscriptions.

Manages in-memory tracking of resource subscriptions and sends
ResourceUpdatedNotification to subscribed clients when resources change.

This follows the MCP decoupled pub/sub model where:
- Clients subscribe to resource URIs
- Server sends notifications ONLY when developer explicitly calls notify_resource_updated()
- Client receives notification and decides whether to fetch updated content
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

import mcp.types
from pydantic import AnyUrl

from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from mcp.server.session import ServerSession

logger = get_logger(__name__)


class ResourceSubscriptionManager:
    """Thread-safe in-memory manager for resource subscriptions.

    Tracks which sessions are subscribed to which resource URIs and handles
    sending notifications when resources are updated.

    Example:
        ```python
        manager = ResourceSubscriptionManager()

        # Subscribe a session to a resource
        await manager.subscribe("resource://data/sensor", session)

        # Later, when resource changes, notify subscribers
        await manager.notify_subscribers("resource://data/sensor")

        # Cleanup when session disconnects
        await manager.cleanup_on_disconnect(session)
        ```
    """

    def __init__(self) -> None:
        """Initialize the subscription manager with empty tracking."""
        # Map of URI -> set of subscribed sessions
        self._subscriptions: dict[str, set[ServerSession]] = {}
        # Lock for thread-safe access to subscriptions dict
        self._lock = asyncio.Lock()

    async def subscribe(self, uri: str | AnyUrl, session: ServerSession) -> None:
        """Subscribe a session to receive updates for a resource URI.

        Args:
            uri: The resource URI to subscribe to
            session: The MCP server session to register
        """
        uri_str = str(uri)
        async with self._lock:
            if uri_str not in self._subscriptions:
                self._subscriptions[uri_str] = set()
            self._subscriptions[uri_str].add(session)
            logger.debug(f"Session subscribed to resource: {uri_str}")

    async def unsubscribe(self, uri: str | AnyUrl, session: ServerSession) -> None:
        """Unsubscribe a session from a resource URI.

        Args:
            uri: The resource URI to unsubscribe from
            session: The MCP server session to unregister
        """
        uri_str = str(uri)
        async with self._lock:
            if uri_str in self._subscriptions:
                self._subscriptions[uri_str].discard(session)
                # Clean up empty sets
                if not self._subscriptions[uri_str]:
                    del self._subscriptions[uri_str]
                logger.debug(f"Session unsubscribed from resource: {uri_str}")

    async def notify_subscribers(self, uri: str | AnyUrl) -> None:
        """Send ResourceUpdatedNotification to all sessions subscribed to a URI.

        This method should be called by developers when their resource content
        changes and they want to notify subscribed clients.

        Args:
            uri: The resource URI that was updated
        """
        uri_str = str(uri)

        # Copy sessions under lock to avoid holding lock during notification sends
        async with self._lock:
            sessions = list(self._subscriptions.get(uri_str, set()))

        if not sessions:
            logger.debug(f"No subscribers for resource: {uri_str}")
            return

        # Create the notification wrapped in ServerNotification
        notification = mcp.types.ServerNotification(
            mcp.types.ResourceUpdatedNotification(
                params=mcp.types.ResourceUpdatedNotificationParams(uri=AnyUrl(uri_str))
            )
        )

        # Send to all subscribers, don't let individual failures break the loop
        for session in sessions:
            with suppress(Exception):
                await session.send_notification(notification)
                logger.debug(f"Sent resource update notification for: {uri_str}")

    async def cleanup_on_disconnect(self, session: ServerSession) -> None:
        """Remove all subscriptions for a disconnected session.

        Called when a session ends to clean up any lingering subscriptions.
        This is called from the finally block in LowLevelServer.run() to ensure
        cleanup happens for all transports (stdio, SSE, streamable HTTP) and
        all exit scenarios (normal completion, errors, cancellation).

        Args:
            session: The MCP server session that is disconnecting
        """
        async with self._lock:
            # Find and remove the session from all URI subscription sets
            uris_to_clean = []
            for uri, sessions in self._subscriptions.items():
                if session in sessions:
                    sessions.discard(session)
                    if not sessions:
                        uris_to_clean.append(uri)

            # Clean up empty sets
            for uri in uris_to_clean:
                del self._subscriptions[uri]

            if uris_to_clean:
                logger.debug(
                    f"Cleaned up {len(uris_to_clean)} subscriptions on disconnect"
                )

    async def get_subscriptions_count(self) -> int:
        """Get the total number of active subscriptions (for testing/debugging).

        Returns:
            Total count of (uri, session) pairs
        """
        async with self._lock:
            return sum(len(sessions) for sessions in self._subscriptions.values())

    async def is_subscribed(self, uri: str | AnyUrl, session: ServerSession) -> bool:
        """Check if a session is subscribed to a URI (for testing/debugging).

        Args:
            uri: The resource URI to check
            session: The session to check

        Returns:
            True if the session is subscribed to the URI
        """
        uri_str = str(uri)
        async with self._lock:
            return session in self._subscriptions.get(uri_str, set())
