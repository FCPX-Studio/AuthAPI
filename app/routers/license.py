from fastapi import APIRouter, Request, HTTPException

from app.schemas.requests import ActivateLicenseRequest, ActivateTrialRequest
from app.schemas.user import LicenseToken
from app.schemas.exceptions import (
    InvalideLicenseKeyError,
    ExpiredLicenseError,
    RevokedLicenseError,
    ActivationLimitReachedError,
    TrialNotAvailableError,
    TrialExpiredError,
)
from app.services import Licensing


# Licenses are created from the orders:ready stream (app.events), not over HTTP
router = APIRouter(
    prefix='/license',
    tags=["license"]
)


@router.post('/activate', response_model=LicenseToken)
async def activate(body: ActivateLicenseRequest, request: Request) -> LicenseToken:
    """
    Validate and activate a license for a specific machine

    Args:
        body (ActivateLicenseRequest): License and machine informations
        request (Request): Default request argument

    Returns:
        LicenseToken: Signed token FxPlug can validate

    Raises:
        HTTPException: 404 unknown key or wrong product
        HTTPException: 403 revoked (refund, chargeback, manual) or expired
        HTTPException: 409 no seat left
    """
    licensing: Licensing = request.app.state.licensing

    try:
        token = licensing.activate_license(body)
    except InvalideLicenseKeyError as e:
        raise HTTPException(
            status_code=404, 
            detail=f"Invalid license key '{e.license_key}'"
        )
    except RevokedLicenseError as e:
        raise HTTPException(
            status_code=403,
            detail=f"License revoked on {e.revoked_at} ({e.reason})"
        )
    except ExpiredLicenseError as e:
        raise HTTPException(
            status_code=403,
            detail=f"License expired on {e.expiration_date}"
        )
    except ActivationLimitReachedError as e:
        raise HTTPException(
            status_code=409,
            detail=f"Activation limit reached: {e.activation_limit} / {e.activation_limit} computer registered"
        )

    return token


@router.post('/activate_trial', response_model=LicenseToken)
async def activate_trial(body: ActivateTrialRequest, request: Request) -> LicenseToken:
    """
    Create (or refresh) and activate the trial of a product for a specific machine
    One trial per product and machine, reactivating keeps the original expiration date

    Args:
        body (ActivateTrialRequest): Product and machine informations
        request (Request): Default request argument

    Returns:
        LicenseToken: Signed trial token FxPlug can validate

    Raises:
        HTTPException: 404 unknown product or no trial available, 403 trial expired
    """
    licensing: Licensing = request.app.state.licensing

    try:
        token = licensing.activate_trial(body)
    except TrialNotAvailableError as e:
        raise HTTPException(
            status_code=404,
            detail=f"No trial available for product '{e.product_id}'"
        )
    except TrialExpiredError as e:
        raise HTTPException(
            status_code=403,
            detail=f"Trial expired on {e.expiration_date}"
        )

    return token
