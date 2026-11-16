from config import settings

from app.schemas.requests import (
    PaddleTransactionOrder,
    PaddleTransactionItem,
    ActivateLicenseRequest,
    ActivateTrialRequest,
)
from app.schemas.user import UserLicense, LicenseToken
from app.schemas.product import ProductLicense
from app.schemas.exceptions import (
    InvalideLicenseKeyError,
    ExpiredLicenseError,
    ActivationLimitReachedError,
    ProductLicenseNotFoundError,
    TrialNotAvailableError,
    TrialExpiredError,
)
from app.repositories import LicenseRepository, ProductRepository
from app.keytool import Keytool

from typing import Optional
from datetime import datetime, timezone


class Licensing:
    """
    Manage license creation and validation
    """

    def __init__(self) -> None:
        self.repository = LicenseRepository()
        self.product_repository = ProductRepository()
        self.keytool = Keytool()

    @staticmethod
    def _now() -> datetime:
        """
        Get current datetime, timezone aware (DB dates are returned in UTC)

        Returns:
            datetime: Current datetime
        """
        return datetime.now(timezone.utc)
    
    def _bad_license(self, license: Optional[UserLicense], product_id: str) -> bool:
        """
        Validate a license, reject None and make sure the licensed product match

        Args:
            license (License): License to validate
            product_id: Product FxPlug ID sent by the installer (Product.bundle_id)

        Returns:
            bool: True if bad license, else False
        """
        if not license:
            return True
        
        return license.bundle_id != product_id
    
    def _license_expired(self, license: UserLicense) -> bool:
        """
        Check if a license is expired

        Args:
            license (License): License to check expiration

        Returns:
            bool: True if expired, else False
        """
        if not license.expires_at:
            return False
        
        return self._now() >= license.expires_at

    def _get_product_license(self, paddle_item: PaddleTransactionItem) -> ProductLicense:
        """
        Retrieve the catalog license of an item from its Paddle price

        Args:
            paddle_item (PaddleTransactionItem): Order item (Paddle price and quantity)

        Returns:
            ProductLicense: Catalog license (seats, lifetime, trial flag)

        Raises:
            ProductLicenseNotFoundError: Unknown Paddle price ID
        """
        product_license = self.product_repository.get_product_license(paddle_item.paddle_price_id)
        if product_license is None:
            raise ProductLicenseNotFoundError(paddle_item.paddle_price_id)
        return product_license

    def _create_item_license(
            self,
            paddle_order_id: str,
            paddle_item: PaddleTransactionItem,
            product_license: ProductLicense,
        ) -> UserLicense:
        """
        Build the license for a specific item (not saved in DB)
        Seats and lifetime come from the ProductLicense catalog

        Args:
            paddle_order_id (str): Paddle transaction ID (txn_…)
            paddle_item (PaddleTransactionItem): Order item (Paddle price and quantity)
            product_license (ProductLicense): Catalog license of the item

        Returns:
            UserLicense: Created license
        """
        # Create a unique license key
        while True:
            key = self.keytool.generate_cuuid()
            if not self.repository.license_key_exists(key):
                break
        
        # Build license
        license = paddle_item.build_license(
            key, paddle_order_id, product_license
        )

        return license
    
    def create_licenses(
            self,
            paddle_order: PaddleTransactionOrder
        ) -> list[str]:  
        """
        Create licenses for each items of an order
        Trial items (ProductLicense.is_trial) are skipped: trials are created by the
        installer through /license/activate_trial

        Args:
            paddle_order (PaddleTransactionOrder): Paddle order for which to create licenses

        Returns:
            list[str]: License keys, normalized UUID (XXXX-XXXX-XXXX-XXXX), in the same
                order as the non-trial items (empty if the order only has trials)

        Raises:
            ProductLicenseNotFoundError: An item references an unknown Paddle price ID,
                nothing is saved
        """
        # Prevent duplicate licenses for a single order
        order_licenses = self.repository.get_order_licenses(paddle_order.id)
        if order_licenses:
            return [lcs.key for lcs in order_licenses]

        # Resolve every item first: an unknown price aborts the whole order
        catalog = [
            (paddle_item, self._get_product_license(paddle_item)) 
            for paddle_item in paddle_order.items
        ]

        licenses = [
            self._create_item_license(paddle_order.id, paddle_item, product_license)
            for paddle_item, product_license in catalog
            if not product_license.is_trial
        ]
        if not licenses:
            return []

        # Save the customer and its licenses (single transaction) and return db keys
        return self.repository.save_licenses(paddle_order.customer.build_user(), licenses)
    
    def activate_license(
            self,
            activation_request: ActivateLicenseRequest
        ) -> LicenseToken:
        """
        Validate informations then create a signed token.
        Check for validation
            - Product match
            - License expiration
            - Activations count

        Args:
            license_key (str): User license key
            product_id (str): Product to license
            fingerprint (str): User machine fingerprint

        Returns:
            LicenseToken: Signed license token
        """
        license_key = self.keytool.normalize_cuuid(activation_request.license_key)
        now = self._now()

        # Validate license with product and check expiration date
        license = self.repository.get_license(license_key)

        if self._bad_license(license, activation_request.product_id):
            raise InvalideLicenseKeyError(license_key)
        assert license and license.id # retrieved license should have a ID
        if self._license_expired(license):
            raise ExpiredLicenseError(license.key, license.expires_at)

        # Check for activation limit and update
        past_activations = self.repository.get_license_activations(license.key)
        fingerprint_activations = { pa.machine_fingerprint: pa for pa in past_activations }
        user_current_activation = fingerprint_activations.get(activation_request.machine_fingerprint)

        activation = activation_request.build_activation()

        if user_current_activation:
            assert user_current_activation.id # retrieved activations should have a ID
            activation_id = self.repository.update_activation(user_current_activation.id, activation)
        elif len(past_activations) < license.activation_seats:
            activation_id = self.repository.save_activation(license.key, activation)
            fingerprint_activations[activation.machine_fingerprint] = activation
        else:
            raise ActivationLimitReachedError(license.key, license.activation_seats)

        # IDs stay strings in the token and response: contract with the shipped plugins
        signed_activation = self.keytool.create_signed_license_activation(
            format_version=settings.LICENSE_TOKEN_FORMAT_VERSION,
            license_id=str(license.id),
            product_id=activation_request.product_id,
            fingerprint=activation.machine_fingerprint,
            issued_at=now,
            expire_at=license.expires_at
        )

        return LicenseToken(
            activation_id=str(activation_id),
            activation_seats=license.activation_seats,
            activations_used=len(fingerprint_activations),
            license=signed_activation
        )

    def activate_trial(
            self,
            activation_request: ActivateTrialRequest
        ) -> LicenseToken:
        """
        Create or refresh the trial of a product on a machine, then create a signed token.
        One trial per product and machine: reactivating keeps the original expiration date
        Check for validation
            - Product offers a trial (Product.trial_duration)
            - Trial expiration

        Args:
            activation_request (ActivateTrialRequest): Product and machine informations

        Returns:
            LicenseToken: Signed trial token (expires at the end of the trial)

        Raises:
            TrialNotAvailableError: Unknown product or no trial for this product
            TrialExpiredError: This machine already used its trial for this product
        """
        bundle_id = activation_request.product_id
        now = self._now()

        product_trial = self.product_repository.get_trial(bundle_id)
        if product_trial is None:
            raise TrialNotAvailableError(bundle_id)

        trial = self.repository.upsert_trial(
            product_id=product_trial.product_id,
            activation=activation_request.build_activation(),
            expires_at=now + product_trial.trial_duration,
        )
        if now >= trial.expires_at:
            raise TrialExpiredError(bundle_id, trial.expires_at)

        # IDs stay strings in the token and response: contract with the shipped plugins
        signed_activation = self.keytool.create_signed_license_activation(
            format_version=settings.LICENSE_TOKEN_FORMAT_VERSION,
            license_id=str(trial.id),
            product_id=bundle_id,
            fingerprint=trial.machine_fingerprint,
            issued_at=now,
            expire_at=trial.expires_at
        )

        # A trial is bound to a single machine
        return LicenseToken(
            activation_id=str(trial.id),
            activation_seats=1,
            activations_used=1,
            license=signed_activation
        )
