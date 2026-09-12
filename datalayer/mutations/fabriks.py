"""Upload and read grants for fabriks stores.

The same four mutations every store type has, with one difference worth knowing: finishing a
fabriks upload is not bookkeeping. `fill_info` reads the store's manifest, so an interrupted
upload -- which presents as a prefix with no `fabriks.json`, because the writer lands it last --
is refused here rather than registering as a store that a renderer later discovers is broken.
"""

from kante.types import Info
from typing import cast
from datalayer import inputs, types
from datalayer.datalayer import get_current_datalayer
from datalayer import models


def request_fabriks_upload(info: Info, input: inputs.RequestFabriksUploadInput) -> types.FabriksUploadGrant:
    """Request temporary S3 upload credentials for a fabriks store's prefix."""
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return types.FabriksUploadGrant.from_pydantic(dl.generate_fabriks_upload_grant(info.context.request.organization.id, input_model))


def finish_fabriks_upload(info: Info, input: inputs.FinishFabriksUploadInput) -> types.FabriksStore:
    """Mark the FabriksStore populated, reading its manifest to learn what was written."""
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return cast(types.FabriksStore, dl.finish_fabriks_upload(info.context.request.organization.id, input_model))


def request_fabriks_access(info: Info, input: inputs.RequestFabriksAccessInput) -> types.FabriksAccessGrant:
    """Request temporary S3 read credentials covering a fabriks store's whole prefix."""
    dl = get_current_datalayer()

    model = input.to_pydantic()

    store = models.FabriksStore.objects.get(id=model.store_id, organization=info.context.request.organization)
    return types.FabriksAccessGrant.from_pydantic(dl.generate_fabriks_access_grant(store))


def request_general_fabriks_access(info: Info, input: inputs.RequestGeneralFabriksAccessInput) -> types.GeneralFabriksAccessGrant:
    """Request organization-wide temporary S3 read credentials for fabriks stores."""
    dl = get_current_datalayer()
    input.to_pydantic()

    return types.GeneralFabriksAccessGrant.from_pydantic(dl.generate_general_fabriks_access_grant(info.context.request.organization.id, info.context.request.user.id))
