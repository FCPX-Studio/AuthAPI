"""
Redis stream handlers:
- orders:ready -> UserLicense -> orders:licensed + emails:license
- orders:refunded -> revoke UserLicense

Handlers must be idempotent: EventConsumer delivers at least once and acks only when the handler
returns. Any exception leaves the message pending: it is retried, then moved to `<stream>:dead`.
"""
from events import EventPublisher, OrderLicensed, OrderReady, OrderRefunded, SendLicenseEmail
from events.streams import LICENSE_EMAILS, ORDERS_LICENSED

from app.services import Licensing

from logger import logger

import asyncio


class OrderReadyHandler:
    """
    Issue the licenses of an order committed by PaddleSync, then announce them
    """

    def __init__(self, licensing: Licensing, publisher: EventPublisher | None = None) -> None:
        self.licensing = licensing
        self.publisher = publisher or EventPublisher()

    async def __call__(self, event: OrderReady) -> None:
        """
        Args:
            event (OrderReady): Order and its items are in DB
        """
        # Sync SQLAlchemy: run off the event loop, which also serves the HTTP endpoints
        order = await asyncio.to_thread(self.licensing.create_licenses, event.order_id)

        # Published before returning, so a crash replays the event (consumers dedupe on the ids).
        # Always published, even for a trial-only or fully refunded order, so the website stops waiting.
        # Carries the license keys (items): never log the event or its items, counts only
        await self.publisher.publish(
            ORDERS_LICENSED,
            OrderLicensed(
                order_id=order.order_id,
                paddle_order_id=order.paddle_order_id,
                user_license_ids=order.user_license_ids,
                items=order.items,
                claim_hash=order.claim_hash,
            ),
        )
        if order.user_license_ids:
            await self.publisher.publish(
                LICENSE_EMAILS,
                SendLicenseEmail(order_id=order.order_id, user_license_ids=order.user_license_ids),
            )

        # claim=yes/no only: never the hash itself
        logger.info(
            "Issued %d license(s) for order %s (%s), %d item(s), claim=%s",
            len(order.user_license_ids),
            order.order_id,
            order.paddle_order_id,
            len(order.items),
            "yes" if order.claim_hash else "no",
        )


class OrderRefundedHandler:
    """
    Revoke the licenses of line items refunded or charged back in Paddle (committed by PaddleSync)
    """

    def __init__(self, licensing: Licensing) -> None:
        self.licensing = licensing

    async def __call__(self, event: OrderRefunded) -> None:
        """
        Licenses not issued yet need nothing: issuing skips refunded items (OrderItem.refunded_at)

        Args:
            event (OrderRefunded): Refunded line items of an order
        """
        # Sync SQLAlchemy: run off the event loop, which also serves the HTTP endpoints
        revoked = await asyncio.to_thread(self.licensing.revoke_licenses, event.paddle_item_ids, event.reason)

        logger.info(
            "Revoked %d license(s) of order %s (%s), %s of %d item(s)",
            len(revoked),
            event.order_id,
            event.paddle_order_id,
            event.reason,
            len(event.paddle_item_ids),
        )
