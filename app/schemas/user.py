from pydantic import AwareDatetime, BaseModel

from typing import Optional


class User(BaseModel):
    id: Optional[int] = None                        # Set by the DB
    paddle_customer_id: str                         # Paddle customer ID (ctm_…)
    email: str
    name: Optional[str] = None
    marketing: bool = False
    subscribed_at: Optional[AwareDatetime] = None    # Set by the DB on first insert

class UserLicense(BaseModel):
    id: Optional[int] = None                        # Set by the DB
    key: str
    product_license_id: int
    paddle_order_id: str                            # Paddle transaction ID (txn_…)
    user_id: Optional[int] = None                   # Set when the owner is saved
    activation_seats: int
    created_at: AwareDatetime
    expires_at: Optional[AwareDatetime] = None
    bundle_id: Optional[str] = None                 # Licensed product (FxPlug ID), filled on read

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
    payload: str
    signature_algorithm: str
    signature: bytes

class LicenseToken(BaseModel):
    activation_id: str                              # Kept as a string: API contract with the installer
    activation_seats: int
    activations_used: int
    license: SignedActivation
