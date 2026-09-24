from kante.types import Info
from typing import cast
from datalayer import types, inputs
from datalayer.datalayer import get_current_datalayer
from datalayer import models


def request_media_upload(info: Info, input: inputs.RequestMediaUploadInput) -> types.MediaUploadGrant:
    """Request temporary S3 upload credentials for a media file."""

    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return types.MediaUploadGrant(**dl.generate_media_upload_grant(info.context.request.organization.id, input_model, user=info.context.request.user).model_dump())


def finish_media_upload(info: Info, input: inputs.FinishMediaUploadInput) -> types.MediaStore:
    """Mark the MediaStore as populated after a successful upload."""

    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return cast(types.MediaStore, dl.finish_media_upload(info.context.request.organization.id, input_model))


def request_media_access(info: Info, input: inputs.RequestMediaAccessInput) -> types.MediaAccessGrant:
    """Request temporary S3 read credentials for a media file."""

    dl = get_current_datalayer()
    model = input.to_pydantic()

    store = models.MediaStore.objects.get(id=model.store_id, organization=info.context.request.organization)
    return types.MediaAccessGrant.from_pydantic(dl.generate_media_access_grant(store))


def request_general_media_access(info: Info, input: inputs.RequestGeneralMediaAccessInput) -> types.GeneralMediaAccessGrant:
    """Request temporary S3 read credentials for a media file."""

    dl = get_current_datalayer()
    model = input.to_pydantic()

    return types.GeneralMediaAccessGrant.from_pydantic(dl.generate_general_media_access_grant(info.context.request.organization.id, info.context.request.user.id))
