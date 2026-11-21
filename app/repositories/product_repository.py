from db import Database
from db.models import ProductLicenseRow, ProductRow

from app.schemas.product import ProductTrial

from typing import Optional

from sqlalchemy import func, select


class ProductRepository(Database):
    """
    Read-only access to the product catalog (written by PaddleSync)
    """

    # --- GET

    def get_trial(self, bundle_id: str) -> Optional[ProductTrial]:
        """
        Retrieve the trial offered by a product: its active trial licenses (Paddle prices with a trial
        period), the longest one when there are several. An archived product (Paddle "delete") or
        archived trial license offers no trial

        Args:
            bundle_id (str): Extension ID (Product.bundle_id), sent by the installer

        Returns:
            Optional[ProductTrial]: Trial offer, None if the product is unknown, archived or has no active trial
        """
        with self.session() as s:
            row = s.execute(
                select(ProductRow.id, func.max(ProductLicenseRow.life_duration).label("trial_duration"))
                .join(ProductLicenseRow, ProductLicenseRow.product_id == ProductRow.id)
                .where(
                    ProductRow.bundle_id == bundle_id,
                    ProductRow.archived.is_(False),
                    ProductLicenseRow.is_trial.is_(True),
                    ProductLicenseRow.archived.is_(False),
                    ProductLicenseRow.life_duration.is_not(None),
                )
                .group_by(ProductRow.id)
            ).one_or_none()
            if row is None:
                return None
            return ProductTrial(product_id=row.id, bundle_id=bundle_id, trial_duration=row.trial_duration)
