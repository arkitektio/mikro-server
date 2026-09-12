from kante.types import Info
from typing import cast
from datalayer import inputs, types
from datalayer.datalayer import get_current_datalayer
from datalayer import models


def request_zarr_upload(info: Info, input: inputs.RequestZarrUploadInput) -> types.ZarrUploadGrant:
    """Request temporary S3 upload credentials for a Zarr store."""
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return types.ZarrUploadGrant.from_pydantic(dl.generate_zarr_upload_grant(info.context.request.organization.id, input_model))


def finish_zarr_upload(info: Info, input: inputs.FinishZarrUploadInput) -> types.ZarrStore:
    """Mark the ZarrStore as populated after a successful upload."""
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return cast(types.ZarrStore, dl.finish_zarr_upload(info.context.request.organization.id, input_model))


def refresh_zarr_upload(info: Info, input: inputs.RefreshZarrUploadInput) -> types.ZarrUploadGrant:
    """Reissue upload credentials for a Zarr store whose upload is still in flight."""
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return types.ZarrUploadGrant.from_pydantic(dl.refresh_zarr_upload_grant(info.context.request.organization.id, input_model.store_id))


def request_zarr_access(info: Info, input: inputs.RequestZarrAccessInput) -> types.ZarrAccessGrant:
    """Request temporary S3 read credentials for a Zarr store."""
    dl = get_current_datalayer()

    model = input.to_pydantic()

    store = models.ZarrStore.objects.get(id=model.store_id, organization=info.context.request.organization)
    return types.ZarrAccessGrant.from_pydantic(dl.generate_zarr_access_grant(store))


def request_general_zarr_access(info: Info, input: inputs.RequestGeneralZarrAccessInput) -> types.GeneralZarrAccessGrant:
    """Request temporary S3 read credentials for a media file."""

    dl = get_current_datalayer()
    model = input.to_pydantic()

    return types.GeneralZarrAccessGrant.from_pydantic(dl.generate_general_zarr_access_grant(info.context.request.organization.id, info.context.request.user.id))
