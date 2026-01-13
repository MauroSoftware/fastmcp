"""Resource subscription manager for MCP notifications.

Manages subscriptions to resource updates and sends notifications when resources change.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import suppress
from typing import TYPE_CHECKING

import mcp.types
from mcp.types import ResourceUpdatedNotification, ResourceUpdatedNotificationParams

from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from mcp.server.session import ServerSession

logger = get_logger(__name__)


class ResourceSubscriptionManager:
    """Manages resource subscriptions and notifications.
    
    Thread-safe, in-memory storage for resource subscriptions.
    Allows clients to subscribe to resource updates and receive notifications
    when resources change.
    """

    def __init__(self) -> None:
        """Initialize the subscription manager."""
        # Maps URI to set of sessions subscribed to that resource
        self._subscriptions: dict[str, set[ServerSession]] = defaultdict(set)
        # Lock for thread-safe access to subscriptions
        self._lock = asyncio.Lock()

    async def subscribe(self, uri: str, session: ServerSession) -> None:
        """Subscribe a session to resource updates.
        
        Args:
            uri: The resource URI to subscribe to
            session: The ServerSession that wants to receive updates
        """
        async with self._lock:
            self._subscriptions[uri].add(session)
            logger.debug(f"Session subscribed to resource: {uri}")

    async def unsubscribe(self, uri: str, session: ServerSession) -> None:
        """Unsubscribe a session from resource updates.
        
        Args:
            uri: The resource URI to unsubscribe from
            session: The ServerSession to unsubscribe
        """
        async with self._lock:
            if uri in self._subscriptions:
                self._subscriptions[uri].discard(session)
                # Clean up empty subscription sets
                if not self._subscriptions[uri]:
                    del self._subscriptions[uri]
                logger.debug(f"Session unsubscribed from resource: {uri}")

    async def notify_subscribers(self, uri: str) -> None:
        """Notify all subscribers that a resource has been updated.
        
        Args:
            uri: The URI of the resource that was updated
        """
        async with self._lock:
            sessions = self._subscriptions.get(uri, set()).copy()
        
        if not sessions:
            return
        
        notification = ResourceUpdatedNotification(
            params=ResourceUpdatedNotificationParams(uri=uri),
        )
        
        # Send notifications to all subscribers
        # Use suppress to prevent notification failures from breaking the system
        for session in sessions:
            with suppress(Exception):
                await session.send_notification(notification)  # type: ignore[arg-type]
        
        logger.debug(f"Notified {len(sessions)} subscriber(s) of resource update: {uri}")

    async def cleanup_on_disconnect(self, session: ServerSession) -> None:
        """Remove all subscriptions for a disconnected session.
        
        Args:
            session: The ServerSession that disconnected
        """
        async with self._lock:
            # Find all URIs this session is subscribed to
            uris_to_remove = []
            for uri, sessions in list(self._subscriptions.items()):
                if session in sessions:
                    sessions.discard(session)
                    # Clean up empty subscription sets
                    if not sessions:
                        uris_to_remove.append(uri)
            
            # Remove empty subscription sets
            for uri in uris_to_remove:
                del self._subscriptions[uri]
            
            if uris_to_remove:
                logger.debug(
                    f"Cleaned up {len(uris_to_remove)} subscription(s) for disconnected session"
                )
