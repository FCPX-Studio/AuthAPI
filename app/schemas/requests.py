from app.schemas.user import UserActivation

from pydantic import BaseModel
from typing import Optional


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
