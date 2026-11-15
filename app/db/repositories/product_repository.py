from app.db.database import Repository
from db.models import ProductLicenseRow, ProductRow

from app.schemas.product import ProductLicense, ProductTrial

from typing import Optional

from sqlalchemy import select


class ProductRepository(Repository):
    """
    Read-only access to the product catalog (written by PaddleSync)
    """

    # --- MAPPING

    @staticmethod
    def _to_product_license(row: ProductLicenseRow) -> ProductLicense:
        return ProductLicense(
            id=row.id,
            product_id=row.product_id,
            paddle_price_id=row.paddle_price_id,
            is_trial=row.is_trial,
            activation_seats=row.activation_seats,
            life_duration=row.life_duration,
        )

    # --- GET

    def get_product_license(self, paddle_price_id: str) -> Optional[ProductLicense]:
        """
        Retrieve a license offered on the website from its Paddle price ID

        Args:
            paddle_price_id (str): Paddle price ID (pri_…)

        Returns:
            Optional[ProductLicense]: Corresponding product license, None if unknown
        """
        with self.session() as s:
            row = s.scalar(select(ProductLicenseRow).where(ProductLicenseRow.paddle_price_id == paddle_price_id))
            return self._to_product_license(row) if row else None

    def get_trial(self, bundle_id: str) -> Optional[ProductTrial]:
        """
        Retrieve the trial offered by a product

        Args:
            bundle_id (str): Product FxPlug ID, sent by the installer

        Returns:
            Optional[ProductTrial]: Trial offer, None if the product is unknown or has no trial
        """
        with self.session() as s:
            row = s.execute(
                select(ProductRow.id, ProductRow.trial_duration).where(ProductRow.bundle_id == bundle_id)
            ).one_or_none()
            if row is None or row.trial_duration is None:
                return None
            return ProductTrial(product_id=row.id, bundle_id=bundle_id, trial_duration=row.trial_duration)
