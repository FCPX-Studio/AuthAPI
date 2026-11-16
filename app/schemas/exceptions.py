from typing import Optional
from datetime import datetime


class InvalideLicenseKeyError(Exception):
    def __init__(self, license_key: str) -> None:
        self.license_key = license_key
        super().__init__(f"Invalide license key '{license_key}'")

class ExpiredLicenseError(Exception):
    def __init__(self, license_key: str, expiration_date: Optional[datetime]) -> None:
        self.license_key = license_key
        self.expiration_date = expiration_date
        super().__init__(f"License '{license_key}' expired on {expiration_date}")

class ActivationNotFoundError(Exception):
    def __init__(self, activation_id: int) -> None:
        self.activation_id = activation_id
        super().__init__(f"Invalide activation ID '{activation_id}'")

class ActivationLimitReachedError(Exception):
    def __init__(self, license_key: str, activation_limit: int) -> None:
        self.license_key = license_key
        self.activation_limit = activation_limit
        super().__init__(f"Activations limit reached ({activation_limit}) for license '{license_key}'")

class OrderNotFoundError(Exception):
    """
    The order of an OrderReady event is not in the DB. PaddleSync publishes after its commit,
    so this is not expected: the event is retried, then dead-lettered
    """
    def __init__(self, order_id: int) -> None:
        self.order_id = order_id
        super().__init__(f"Order {order_id} not found")

class TrialNotAvailableError(Exception):
    def __init__(self, product_id: str) -> None:
        self.product_id = product_id
        super().__init__(f"No trial available for product '{product_id}'")

class TrialExpiredError(Exception):
    def __init__(self, product_id: str, expiration_date: datetime) -> None:
        self.product_id = product_id
        self.expiration_date = expiration_date
        super().__init__(f"Trial of product '{product_id}' expired on {expiration_date}")
