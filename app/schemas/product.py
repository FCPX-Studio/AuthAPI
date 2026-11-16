from pydantic import BaseModel

from datetime import timedelta


class ProductTrial(BaseModel):
    """Trial offered by a product: the longest of its active trial licenses (ProductLicense.is_trial)"""
    product_id: int
    bundle_id: str                                  # FxPlug ID, sent by the installer
    trial_duration: timedelta
