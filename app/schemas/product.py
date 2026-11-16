from pydantic import BaseModel

from datetime import timedelta


class ProductTrial(BaseModel):
    """Trial offered by a product (Product.trial_duration is set)"""
    product_id: int
    bundle_id: str                                  # FxPlug ID, sent by the installer
    trial_duration: timedelta
