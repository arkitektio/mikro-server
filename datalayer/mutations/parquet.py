from kante.types import Info
from typing import cast
from datalayer import inputs, types
from datalayer.datalayer import get_current_datalayer
from datalayer import models


def request_parquet_upload(info: Info, input: inputs.RequestParquetUploadInput) -> types.ParquetUploadGrant:
    """Request temporary S3 upload credentials for a parquet store."""
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return types.ParquetUploadGrant.from_pydantic(dl.generate_parquet_upload_grant(info.context.request.organization.id, input_model, user=info.context.request.user))


def finish_parquet_upload(info: Info, input: inputs.FinishParquetUploadInput) -> types.ParquetStore:
    """Mark the ParquetStore as populated after a successful upload."""
    dl = get_current_datalayer()
    input_model = input.to_pydantic()
    return cast(types.ParquetStore, dl.finish_parquet_upload(info.context.request.organization.id, input_model))


def request_parquet_access(info: Info, input: inputs.RequestParquetAccessInput) -> types.ParquetAccessGrant:
    """Request temporary S3 read credentials for a parquet file."""
    dl = get_current_datalayer()

    model = input.to_pydantic()

    store = models.ParquetStore.objects.get(id=model.store_id, organization=info.context.request.organization)
    return types.ParquetAccessGrant.from_pydantic(dl.generate_parquet_access_grant(store))


def request_general_parquet_access(info: Info, input: inputs.RequestGeneralParquetAccessInput) -> types.GeneralParquetAccessGrant:
    """Request temporary S3 read credentials for a parquet file."""

    dl = get_current_datalayer()
    model = input.to_pydantic()

    return types.GeneralParquetAccessGrant.from_pydantic(dl.generate_general_parquet_access_grant(info.context.request.organization.id, info.context.request.user.id))
