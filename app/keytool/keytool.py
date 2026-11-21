from config import settings
from app.schemas.user import SignedActivation

import secrets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import base64
import json
from datetime import datetime, timezone
from typing import Literal, Optional

# Token `kind`: tells the product whether `licenseId` is a UserLicense.id or a UserTrial.id
TokenKind = Literal["license", "trial"]


class Keytool:

    CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    LICENSE_PRIVATE_KEY: Ed25519PrivateKey

    def __init__(self) -> None:
        self.LICENSE_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(settings.LICENSE_PRIVATE_KEY.get_secret_value())
        )

    @staticmethod
    def _iso_dt(dt: datetime) -> str:
        """
        Convert datetime to ISO 8601 UTC for Swift compatibility
        """
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def generate_cuuid(self) -> str:
        """
        Generate a Base32 crockford UUID

        Returns:
            str: normalized UUID string
        """
        chars = "".join(
            secrets.choice(self.CROCKFORD_ALPHABET) 
            for _ in range(16)
        )

        return "-".join(
            chars[i:i + 4] 
            for i in range(0, 16, 4)
        )
    
    def normalize_cuuid(self, key: str) -> str:
        """
        Normalize Base32 crockford UUID

        Args:
            key (str): Crockford UUID to normalize

        Returns:
            str: Normalized UUID
        """
        return key.strip().upper()
    
    def create_signed_license_activation(
            self,
            format_version: int,
            key_id: str,
            kind: TokenKind,
            license_id: str,
            product_id: str,
            fingerprint: str,
            issued_at: datetime,
            expire_at: Optional[datetime]
        ) -> SignedActivation:
        """
        Create a signed license activation. Encode license and user machine info into a payload and sign it with the private key

        Args:
            format_version (int): Signing format version
            key_id (str): Public label of the signing key pair, the product picks its public key from it
            kind (TokenKind): "license" (licenseId = UserLicense.id) or "trial" (licenseId = UserTrial.id)
            license_id (str): ID of the license (or trial) used
            product_id (str): Extension ID linked to the license (Product.bundle_id)
            fingerprint (str): User machine fingerprint
            issued_at (datetime): Date of activation
            expire_at (datetime, optional): Date of license expiration, None = perpetual

        Returns:
            SignedActivation: Base64 payload and its Ed25519 signature
        """
        token = {
            "formatVersion": format_version,
            "keyId": key_id,
            "kind": kind,
            "licenseId": license_id,
            "productId": product_id,
            "machineFingerprint": fingerprint,
            "issuedAt": self._iso_dt(issued_at),
            "expiresAt": self._iso_dt(expire_at) if expire_at else None
        }

        payload = json.dumps(
            token, 
            separators=(",", ":")
        ).encode("utf-8")
        
        # Raw 64 bytes (format expected by CryptoKit), base64 encoded like the payload so it fits in JSON
        signature = self.LICENSE_PRIVATE_KEY.sign(payload)

        return SignedActivation(
            payload=base64.b64encode(payload).decode(),
            signature_algorithm="Ed25519",
            signature=base64.b64encode(signature).decode()
        )
