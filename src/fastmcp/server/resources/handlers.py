"""MCP protocol handlers for resource subscriptions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp.utilities.logging import get_logger

if TYPE_CHECKING:
    from mcp.server.session import ServerSession

    from fastmcp.server.server import FastMCP

logger = get_logger(__name__)


async def handle_subscribe_resource_request(
    uri: str,
    session: ServerSession,
    fastmcp: FastMCP,
) -> None:
    """Handle resources/subscribe request.

    Args:
        uri: The resource URI to subscribe to
        session: The ServerSession making the request
        fastmcp: The FastMCP server instance
    """
    await fastmcp._resource_subscription_manager.subscribe(uri, session)


async def handle_unsubscribe_resource_request(
    uri: str,
    session: ServerSession,
    fastmcp: FastMCP,
) -> None:
    """Handle resources/unsubscribe request.

    Args:
        uri: The resource URI to unsubscribe from
        session: The ServerSession making the request
        fastmcp: The FastMCP server instance
    """
    await fastmcp._resource_subscription_manager.unsubscribe(uri, session)
