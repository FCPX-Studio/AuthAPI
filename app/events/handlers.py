"""
Redis stream handlers: orders:ready -> UserLicense -> licenses:issued + emails:license

Handlers must be idempotent: EventConsumer delivers at least once and acks only when the handler
returns. Any exception leaves the message pending: it is retried, then moved to `orders:ready:dead`.
"""
from events import EventPublisher, LicenseIssued, OrderReady, SendLicenseEmail
from events.streams import LICENSE_EMAILS, LICENSES_ISSUED

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
        # Always published, even for a trial-only order, so the website stops waiting
        await self.publisher.publish(
            LICENSES_ISSUED,
            LicenseIssued(
                order_id=order.order_id,
                paddle_order_id=order.paddle_order_id,
                user_license_ids=order.user_license_ids,
            ),
        )
        if order.user_license_ids:
            await self.publisher.publish(
                LICENSE_EMAILS,
                SendLicenseEmail(order_id=order.order_id, user_license_ids=order.user_license_ids),
            )

        logger.info(
            "Issued %d license(s) for order %s (%s)",
            len(order.user_license_ids),
            order.order_id,
            order.paddle_order_id,
        )
