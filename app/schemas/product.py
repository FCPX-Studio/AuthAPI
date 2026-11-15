from pydantic import BaseModel

from typing import Optional
from datetime import timedelta


class ProductLicense(BaseModel):
    id: int
    product_id: int
    paddle_price_id: str                            # Paddle price ID (pri_…)
    is_trial: bool = False
    activation_seats: int
    life_duration: Optional[timedelta] = None

class ProductTrial(BaseModel):
    """Trial offered by a product (Product.trial_duration is set)"""
    product_id: int
    bundle_id: str                                  # FxPlug ID, sent by the installer
    trial_duration: timedelta
