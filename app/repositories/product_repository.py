from db import Database
from db.models import ProductRow

from app.schemas.product import ProductTrial

from typing import Optional

from sqlalchemy import select


class ProductRepository(Database):
    """
    Read-only access to the product catalog (written by PaddleSync)
    """

    # --- GET

    def get_trial(self, bundle_id: str) -> Optional[ProductTrial]:
        """
        Retrieve the trial offered by a product. An archived product (Paddle "delete") offers no trial

        Args:
            bundle_id (str): Product FxPlug ID, sent by the installer

        Returns:
            Optional[ProductTrial]: Trial offer, None if the product is unknown, archived or has no trial
        """
        with self.session() as s:
            row = s.execute(
                select(ProductRow.id, ProductRow.trial_duration).where(
                    ProductRow.bundle_id == bundle_id, ProductRow.archived.is_(False)
                )
            ).one_or_none()
            if row is None or row.trial_duration is None:
                return None
            return ProductTrial(product_id=row.id, bundle_id=bundle_id, trial_duration=row.trial_duration)
