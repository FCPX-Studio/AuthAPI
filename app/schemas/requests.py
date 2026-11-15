from app.schemas.paddle import PaddleCustomer, PaddleTransaction
from app.schemas.user import UserLicense, UserActivation
from app.schemas.product import ProductLicense

from pydantic import BaseModel, AwareDatetime, Field
from typing import Optional, Literal
from datetime import datetime, timezone


class PaddleTransactionItem(BaseModel):
    paddle_price_id: str        # Paddle price ID (pri_…), matches ProductLicense.paddle_price_id
    quantity: int = Field(default=1, ge=1)  # Multiplies the catalog activation seats

    def build_license(
            self,
            key: str,
            order_id: str,
            product_license: ProductLicense,
        ) -> UserLicense:
        """Build UserLicense instance, user_id is set when its owner is saved"""
        now = datetime.now(timezone.utc)
        expires_at = None
        if product_license.life_duration:
            expires_at = now + product_license.life_duration

        return UserLicense(
            key=key,
            product_license_id=product_license.id,
            paddle_order_id=order_id,
            activation_seats=product_license.activation_seats * self.quantity,
            created_at=now,
            expires_at=expires_at
        )
    
class PaddleTransactionOrder(BaseModel):
    """Processed paddle order, with customer retrieved"""
    id: str
    customer: PaddleCustomer
    items: list[PaddleTransactionItem]

class PaddleTransactionCompletedEvent(BaseModel):
    """
    Paddle Billing `transaction.completed` webhook notification
    https://developer.paddle.com/webhooks/transactions/transaction-completed
    """
    event_id: str                           # Paddle event ID (evt_…)
    event_type: Literal["transaction.completed"]
    occurred_at: AwareDatetime
    notification_id: Optional[str] = None   # Paddle notification ID (ntf_…)
    data: PaddleTransaction

    def build_paddle_order(self, customer: PaddleCustomer) -> PaddleTransactionOrder:
        """
        Build the Paddle order: transaction ID, customer and one item per transaction item
        Trial items are kept, they are filtered later from the catalog (ProductLicense.is_trial)

        Args:
            customer (PaddleCustomer): Transaction customer, fetched from Paddle API

        Returns:
            PaddleTransactionOrder: Paddle order, with linked customer
        """
        return PaddleTransactionOrder(
            id=self.data.id,
            customer=customer,
            items=[
                PaddleTransactionItem(
                    paddle_price_id=item.price.id,
                    quantity=item.quantity,
                )
                for item in self.data.items
            ]
        )
    

class ActivationRequestBase(BaseModel):
    """Product and machine informations sent by the installer"""
    product_id: str
    machine_fingerprint: str
    machine_name: str
    app_version: str
    fcpx_version: Optional[str]
    macOS_version: Optional[str]

    def build_activation(self) -> UserActivation:
        """Build UserActivation instance"""
        return UserActivation(
            machine_fingerprint=self.machine_fingerprint,
            machine_name=self.machine_name,
            app_version=self.app_version,
            fcpx_version=self.fcpx_version,
            macos_version=self.macOS_version,
        )


class ActivateLicenseRequest(ActivationRequestBase):
    license_key: str


class ActivateTrialRequest(ActivationRequestBase):
    pass

    