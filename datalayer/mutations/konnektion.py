"""Upload and read grants for konnektion stores.

The same four mutations every store type has, with one difference worth knowing: finishing a
konnektion upload is not bookkeeping. `fill_info` reads the store's manifest, so an interrupted
upload -- which presents as a prefix with no `konnektion.json`, because the writer lands it last
-- is refused here rather than registering as a store that a renderer later discovers is broken.
"""

from kante.types import Info
from typing import cast
from datalayer import inputs, types
from datalayer.datalayer import get_current_datalayer
from datalayer import models


def request_konnektion_upload(info: Info, input: inputs.RequestKonnektionUploadInput) -> types.KonnektionUploadGrant:
    """Request temporary S3 upload credentials for a konnektion store's prefix."""
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return types.KonnektionUploadGrant.from_pydantic(dl.generate_konnektion_upload_grant(info.context.request.organization.id, input_model))


def finish_konnektion_upload(info: Info, input: inputs.FinishKonnektionUploadInput) -> types.KonnektionStore:
    """Mark the KonnektionStore populated, reading its manifest to learn what was written."""
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return cast(types.KonnektionStore, dl.finish_konnektion_upload(info.context.request.organization.id, input_model))


def request_konnektion_access(info: Info, input: inputs.RequestKonnektionAccessInput) -> types.KonnektionAccessGrant:
    """Request temporary S3 read credentials covering a konnektion store's whole prefix."""
    dl = get_current_datalayer()

    model = input.to_pydantic()

    store = models.KonnektionStore.objects.get(id=model.store_id, organization=info.context.request.organization)
    return types.KonnektionAccessGrant.from_pydantic(dl.generate_konnektion_access_grant(store))


def request_general_konnektion_access(info: Info, input: inputs.RequestGeneralKonnektionAccessInput) -> types.GeneralKonnektionAccessGrant:
    """Request organization-wide temporary S3 read credentials for konnektion stores."""
    dl = get_current_datalayer()
    input.to_pydantic()

    return types.GeneralKonnektionAccessGrant.from_pydantic(dl.generate_general_konnektion_access_grant(info.context.request.organization.id, info.context.request.user.id))
