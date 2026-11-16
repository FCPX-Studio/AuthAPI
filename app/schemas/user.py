from pydantic import AwareDatetime, BaseModel

from typing import Optional


class UserLicense(BaseModel):
    id: int
    key: str
    product_license_id: int
    paddle_item_id: str                             # Paddle line item ID (txnitm_…), links to OrderItem
    item_index: int                                 # 1..OrderItem.quantity
    user_id: int
    activation_seats: int
    created_at: AwareDatetime
    expires_at: Optional[AwareDatetime] = None
    revoked_at: Optional[AwareDatetime] = None      # None = active
    revoked_reason: Optional[str] = None            # refund | chargeback | manual
    bundle_id: Optional[str] = None                 # Licensed product (FxPlug ID), filled on read

class OrderLicenses(BaseModel):
    """Licenses of an order, as published in LicenseIssued / SendLicenseEmail"""
    order_id: int
    paddle_order_id: str                            # Paddle transaction ID (txn_…)
    user_license_ids: list[int]                     # Ordered by (paddle_item_id, item_index)

class UserActivation(BaseModel):
    id: Optional[int] = None                        # Set by the DB
    machine_fingerprint: str
    machine_name: str
    app_version: str
    fcpx_version: Optional[str] = None
    macos_version: Optional[str] = None
    last_activated_at: Optional[AwareDatetime] = None

class UserTrial(BaseModel):
    id: int
    product_id: int
    machine_fingerprint: str
    activated_at: AwareDatetime
    expires_at: AwareDatetime
    last_activated_at: AwareDatetime

class SignedActivation(BaseModel):
    payload: str                                    # Base64 of the compact JSON token
    signature_algorithm: str
    signature: str                                  # Base64 of the raw 64-byte Ed25519 signature of the decoded payload

class LicenseToken(BaseModel):
    activation_id: str                              # Kept as a string: API contract with the installer
    activation_seats: int
    activations_used: int
    license: SignedActivation
