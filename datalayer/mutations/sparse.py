"""Upload, finish, refresh and read-access for sparse stores.

The same quartet every store kind has, plus a refresh: a sparse matrix is written as a prefix
of three chunked arrays, and a write that outlives its session token dies partway through --
the problem `refreshZarrUpload` exists for, met sooner here because the matrices are large.
"""

from typing import cast

from kante.types import Info

from datalayer import inputs, models, types
from datalayer.datalayer import get_current_datalayer


def request_sparse_upload(info: Info, input: inputs.RequestSparseUploadInput) -> types.SparseUploadGrant:
    """Request temporary S3 upload credentials for a sparse store."""
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return types.SparseUploadGrant.from_pydantic(dl.generate_sparse_upload_grant(info.context.request.organization.id, input_model))


def finish_sparse_upload(info: Info, input: inputs.FinishSparseUploadInput) -> types.SparseStore:
    """Mark the SparseStore as populated after a successful upload.

    This is where the group's own metadata is read, and where a half-written tree is refused:
    a missing encoding, a missing array, or an `indptr` that contradicts the declared shape all
    fail here rather than surviving as a store a reader later discovers is broken.
    """
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return cast(types.SparseStore, dl.finish_sparse_upload(info.context.request.organization.id, input_model))


def refresh_sparse_upload(info: Info, input: inputs.RefreshSparseUploadInput) -> types.SparseUploadGrant:
    """Reissue upload credentials for a sparse store whose upload is still in flight."""
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return types.SparseUploadGrant.from_pydantic(dl.refresh_sparse_upload_grant(info.context.request.organization.id, input_model.store_id))


def request_sparse_access(info: Info, input: inputs.RequestSparseAccessInput) -> types.SparseAccessGrant:
    """Request temporary S3 read credentials for a sparse store."""
    dl = get_current_datalayer()
    model = input.to_pydantic()
    store = models.SparseStore.objects.get(id=model.store_id, organization=info.context.request.organization)
    return types.SparseAccessGrant.from_pydantic(dl.generate_sparse_access_grant(store))


def request_general_sparse_access(info: Info, input: inputs.RequestGeneralSparseAccessInput) -> types.GeneralSparseAccessGrant:
    """Request temporary S3 read credentials for sparse stores in the organization."""
    dl = get_current_datalayer()
    del input
    return types.GeneralSparseAccessGrant.from_pydantic(
        dl.generate_general_sparse_access_grant(info.context.request.organization.id, info.context.request.user.id)
    )
