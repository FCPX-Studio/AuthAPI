from sqlalchemy.orm import Session, joinedload
from db import Database
from db.models import (
    OrderItemRow,
    OrderRow,
    ProductLicenseRow,
    ProductRow,
    UserLicenseActivationRow,
    UserLicenseRow,
    UserMachineRow,
    UserTrialRow,
)

from app.schemas.user import OrderLicenses, UserLicense, UserActivation, UserTrial

from events import IssuedItem, IssuedKey, IssuedLicenseTerms, IssuedProduct
from app.schemas.exceptions import InvalideLicenseKeyError, ActivationNotFoundError, OrderNotFoundError

from collections import defaultdict
from collections.abc import Callable
from typing import Optional
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert


class LicenseRepository(Database):
    """
    Writes UserLicense, UserLicenseActivation, UserMachine and UserTrial (owned by AuthAPI).
    Reads Order, OrderItem and ProductLicense (owned by PaddleSync)
    """

    # --- MAPPING

    @staticmethod
    def _to_license(row: UserLicenseRow, bundle_id: Optional[str] = None) -> UserLicense:
        return UserLicense(
            id=row.id,
            key=row.license_key,
            product_license_id=row.product_license_id,
            paddle_item_id=row.paddle_item_id,
            item_index=row.item_index,
            user_id=row.user_id,
            activation_seats=row.activation_seats,
            created_at=row.created_at,
            expires_at=row.expires_at,  # type: ignore[arg-type]
            revoked_at=row.revoked_at,  # type: ignore[arg-type]
            revoked_reason=row.revoked_reason,
            bundle_id=bundle_id,
        )

    @staticmethod
    def _to_activation(row: UserLicenseActivationRow) -> UserActivation:
        # Machine must be loaded (joinedload) or the row still attached to a session
        return UserActivation(
            id=row.id,
            machine_fingerprint=row.machine.machine_fingerprint,
            machine_name=row.machine.machine_name,  # type: ignore[arg-type]
            app_version=row.app_version,  # type: ignore[arg-type]
            fcpx_version=row.fcpx_version,
            macos_version=row.macos_version,
            last_activated_at=row.last_activated_at,
        )

    @staticmethod
    def _to_trial(row: UserTrialRow, machine_fingerprint: str) -> UserTrial:
        return UserTrial(
            id=row.id,
            product_id=row.product_id,
            machine_fingerprint=machine_fingerprint,
            activated_at=row.activated_at,
            expires_at=row.expires_at,
            last_activated_at=row.last_activated_at,
        )

    # --- HELPERS

    @staticmethod
    def _upsert_machine(s: Session, activation: UserActivation) -> int:
        """
        Create the machine from its fingerprint, or refresh its name if already known
        (a machine is shared by every license and trial activated on it)
        Runs inside the caller's session / transaction

        Args:
            s (Session): Open session
            activation (UserActivation): Activation holding the machine informations

        Returns:
            int: Machine ID
        """
        stmt = insert(UserMachineRow).values(
            machine_fingerprint=activation.machine_fingerprint,
            machine_name=activation.machine_name,
        )
        return s.execute(
            stmt.on_conflict_do_update(
                index_elements=[UserMachineRow.machine_fingerprint],
                set_=dict(machine_name=stmt.excluded.machine_name),
            ).returning(UserMachineRow.id)
        ).scalar_one()

    # --- GET

    def get_license(self, license_key: str) -> Optional[UserLicense]:
        """
        Retrieve a license from its key, with the FxPlug ID of the licensed product
        (UserLicense -> ProductLicense -> Product)

        Args:
            license_key (str): License key

        Returns:
            Optional[UserLicense]: Corresponding license, None if the key is unknown
        """
        with self.session() as s:
            row = s.execute(
                select(UserLicenseRow, ProductRow.bundle_id)
                .join(UserLicenseRow.product_license)
                .join(ProductLicenseRow.product)
                .where(UserLicenseRow.license_key == license_key)
            ).one_or_none()
            return self._to_license(row.UserLicenseRow, row.bundle_id) if row else None

    def get_license_activations(self, license_key: str) -> list[UserActivation]:
        """
        Retrieve activations of a license

        Args:
            license_key (str): Licence key

        Returns:
            list[UserActivation]: List of all activations for this license
        """
        with self.session() as s:
            rows = s.scalars(
                select(UserLicenseActivationRow)
                .join(UserLicenseActivationRow.license)
                .options(joinedload(UserLicenseActivationRow.machine))
                .where(UserLicenseRow.license_key == license_key)
                .order_by(UserLicenseActivationRow.last_activated_at)
            ).all()
            return [self._to_activation(r) for r in rows]

    # --- UPDATE

    def update_activation(
            self,
            activation_id: int,
            activation: UserActivation
        ) -> int:
        """
        Update informations for a specific activation
        Only app_version, fcpx_version, macos_version and last_activated_at are updated

        Args:
            activation_id (int): ID of the activation to update
            activation (UserActivation): New activation content

        Returns:
            int: Updated activation ID (same as activation_id)

        Raises:
            ActivationNotFoundError: No activation with this ID
        """
        with self.session() as s:
            updated_id = s.scalar(
                update(UserLicenseActivationRow)
                .where(UserLicenseActivationRow.id == activation_id)
                .values(
                    app_version=activation.app_version,
                    fcpx_version=activation.fcpx_version,
                    macos_version=activation.macos_version,
                    last_activated_at=func.now(),
                )
                .returning(UserLicenseActivationRow.id)
            )
            if updated_id is None:
                raise ActivationNotFoundError(activation_id)
            return updated_id

    # --- WRITE

    @staticmethod
    def _to_issued_item(item: OrderItemRow, keys: list[IssuedKey]) -> IssuedItem:
        """
        A line item as published in LicenseIssued. Its product_license, product and version must be loaded
        """
        product_license = item.product_license
        product = product_license.product
        return IssuedItem(
            paddle_item_id=item.paddle_item_id,
            quantity=item.quantity,
            product=IssuedProduct(
                bundle_id=product.bundle_id,
                name=product.name,
                type=product.type,  # type: ignore[arg-type]  # CHECK (type IN ('plugin', 'asset'))
                version=product.version.label if product.version is not None else None,
            ),
            license=IssuedLicenseTerms(
                paddle_price_id=product_license.paddle_price_id,
                name=product_license.name,
                description=product_license.description,
                activation_seats=product_license.activation_seats,
                life_duration_days=product_license.life_duration.days if product_license.life_duration else None,
                is_trial=product_license.is_trial,
            ),
            keys=keys,
        )

    def issue_order_licenses(
            self,
            order_id: int,
            new_key: Callable[[], str],
            now: datetime
        ) -> OrderLicenses:
        """
        Create the licenses of an order, in a single transaction (all or nothing).
        One UserLicense per unit of each item (item_index 1..quantity), owned by the order's user.
        Skipped items:
        - trial items (ProductLicense.is_trial): trials are created by the installer
          through /license/activate_trial
        - refunded items (OrderItem.refunded_at): the refund arrived before the licenses were issued

        The order's items are locked (FOR SHARE) until the commit: PaddleSync's refund UPDATE of an
        item waits for it, so its OrderRefunded is only handled once these licenses exist (and revokes
        them), or this sees the item already refunded and skips it.

        Idempotent: UNIQUE (paddle_item_id, item_index) makes a replay insert nothing,
        and the licenses already created are returned instead (active ones only: a replay never
        announces or emails a revoked license)

        Also builds what LicenseIssued shows the website, from the same rows and transaction: every item
        of the order (trial and refunded ones included) with its product, license terms and active keys.
        An item with no key is a trial, refunded before issuing, or revoked before a replay. All the keys of
        an order are committed here at once, so no key can follow later

        Args:
            order_id (int): Order.id, from the OrderReady event
            new_key (Callable[[], str]): Generates a license key (XXXX-XXXX-XXXX-XXXX)
            now (datetime): Creation date, expires_at = now + ProductLicense.life_duration

        Returns:
            OrderLicenses: Order IDs, every active license ID of the order ordered by
                (paddle_item_id, item_index) (empty if the order only has trials or refunds), and its items

        Raises:
            OrderNotFoundError: Unknown order ID
            IntegrityError: Any other constraint violation (e.g. license key collision),
                the whole transaction is rolled back
        """
        with self.session() as s:
            order = s.scalar(select(OrderRow).where(OrderRow.id == order_id))
            if order is None:
                raise OrderNotFoundError(order_id)

            # Product and version are read for LicenseIssued. Only OrderItem is locked (OF OrderItem)
            items = s.scalars(
                select(OrderItemRow)
                .where(OrderItemRow.order_id == order_id)
                .options(
                    joinedload(OrderItemRow.product_license)
                    .joinedload(ProductLicenseRow.product)
                    .joinedload(ProductRow.version)
                )
                .order_by(OrderItemRow.paddle_item_id)
                .with_for_update(read=True, of=OrderItemRow)
            ).all()

            item_ids: list[str] = []
            licenses: list[dict] = []
            for item in items:
                product_license = item.product_license
                if product_license.is_trial or item.refunded_at is not None:
                    continue

                item_ids.append(item.paddle_item_id)
                expires_at = now + product_license.life_duration if product_license.life_duration else None
                for item_index in range(1, item.quantity + 1):
                    licenses.append(dict(
                        product_license_id=product_license.id,
                        paddle_item_id=item.paddle_item_id,
                        item_index=item_index,
                        user_id=order.user_id,
                        license_key=new_key(),
                        activation_seats=product_license.activation_seats,
                        created_at=now,
                        expires_at=expires_at,
                    ))

            license_ids: list[int] = []
            keys_by_item: dict[str, list[IssuedKey]] = defaultdict(list)
            if licenses:
                # Only this constraint is ignored: a license_key collision still raises
                s.execute(
                    insert(UserLicenseRow)
                    .values(licenses)
                    .on_conflict_do_nothing(index_elements=[UserLicenseRow.paddle_item_id, UserLicenseRow.item_index])
                )
                # The ids and the keys come from the same rows, so they can't disagree
                rows = s.execute(
                    select(
                        UserLicenseRow.id,
                        UserLicenseRow.paddle_item_id,
                        UserLicenseRow.item_index,
                        UserLicenseRow.license_key,
                        UserLicenseRow.expires_at,
                    )
                    .where(UserLicenseRow.paddle_item_id.in_(item_ids), UserLicenseRow.revoked_at.is_(None))
                    .order_by(UserLicenseRow.paddle_item_id, UserLicenseRow.item_index)
                )
                for row in rows:
                    license_ids.append(row.id)
                    keys_by_item[row.paddle_item_id].append(
                        IssuedKey(license_key=row.license_key, item_index=row.item_index, expires_at=row.expires_at)
                    )

            return OrderLicenses(
                order_id=order.id,
                paddle_order_id=order.paddle_order_id,
                user_license_ids=license_ids,
                items=[self._to_issued_item(item, keys_by_item.get(item.paddle_item_id, [])) for item in items],
            )

    def revoke_item_licenses(self, paddle_item_ids: list[str], reason: str, now: datetime) -> list[int]:
        """
        Revoke every license of the given line items (refunded / charged back in Paddle).
        Idempotent: licenses already revoked keep their first revocation date and reason.

        Args:
            paddle_item_ids (list[str]): Paddle line item IDs (txnitm_…, UserLicense.paddle_item_id)
            reason (str): "refund" or "chargeback" (UserLicense.revoked_reason)
            now (datetime): Revocation date

        Returns:
            list[int]: IDs of the licenses revoked by this call (empty on a replay,
                or if the licenses were never issued)
        """
        with self.session() as s:
            return list(s.scalars(
                update(UserLicenseRow)
                .where(UserLicenseRow.paddle_item_id.in_(paddle_item_ids), UserLicenseRow.revoked_at.is_(None))
                .values(revoked_at=now, revoked_reason=reason)
                .returning(UserLicenseRow.id)
            ))

    def save_activation(self, license_key: str, activation: UserActivation) -> int:
        """
        Save a activation in DB, in a single transaction
        The machine is created from its fingerprint, or its name refreshed if already known
        (a machine is shared by every license activated on it)
        If this machine is already activated on the license, the existing row is
        refreshed instead (one seat per machine, safe against concurrent requests)

        Args:
            license_key (str): Key of the license been activated
            activation (UserActivation): Activation informations

        Returns:
            int: Activation ID

        Raises:
            InvalideLicenseKeyError: Unknown license key
        """
        with self.session() as s:
            license_id = s.scalar(select(UserLicenseRow.id).where(UserLicenseRow.license_key == license_key))
            if license_id is None:
                raise InvalideLicenseKeyError(license_key)

            machine_id = self._upsert_machine(s, activation)

            stmt = insert(UserLicenseActivationRow).values(
                license_id=license_id,
                machine_id=machine_id,
                app_version=activation.app_version,
                fcpx_version=activation.fcpx_version,
                macos_version=activation.macos_version,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[UserLicenseActivationRow.license_id, UserLicenseActivationRow.machine_id],
                set_=dict(
                    app_version=stmt.excluded.app_version,
                    fcpx_version=stmt.excluded.fcpx_version,
                    macos_version=stmt.excluded.macos_version,
                    last_activated_at=func.now(),
                ),
            ).returning(UserLicenseActivationRow.id)

            return s.execute(stmt).scalar_one()

    def upsert_trial(
            self,
            product_id: int,
            activation: UserActivation,
            expires_at: datetime
        ) -> UserTrial:
        """
        Create the trial of a product on a machine, or refresh it if this machine
        already has one (one trial per product and machine, safe against concurrent requests)
        On refresh only app_version, fcpx_version, macos_version and last_activated_at
        are updated: activated_at and expires_at keep their original values, so the caller
        must check expiration on the returned trial

        Args:
            product_id (int): Product ID
            activation (UserActivation): Machine and activation informations
            expires_at (datetime): Expiration date, only used if the trial is created

        Returns:
            UserTrial: Created or existing trial
        """
        with self.session() as s:
            machine_id = self._upsert_machine(s, activation)

            stmt = insert(UserTrialRow).values(
                product_id=product_id,
                machine_id=machine_id,
                app_version=activation.app_version,
                fcpx_version=activation.fcpx_version,
                macos_version=activation.macos_version,
                expires_at=expires_at,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[UserTrialRow.product_id, UserTrialRow.machine_id],
                set_=dict(
                    app_version=stmt.excluded.app_version,
                    fcpx_version=stmt.excluded.fcpx_version,
                    macos_version=stmt.excluded.macos_version,
                    last_activated_at=func.now(),
                ),
            ).returning(UserTrialRow)

            row = s.scalars(stmt).one()
            return self._to_trial(row, activation.machine_fingerprint)
