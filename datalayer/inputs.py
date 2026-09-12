from datalayer import base_models
from strawberry.experimental import pydantic


@pydantic.input(model=base_models.RequestMediaUploadInput, all_fields=True)
class RequestMediaUploadInput:
    """
    Docstring for RequestMediaUploadInput
    """

    pass


@pydantic.input(model=base_models.FinishMediaUploadInput, all_fields=True)
class FinishMediaUploadInput:
    """
    Docstring for FinishMediaUploadInput
    """

    pass


@pydantic.input(model=base_models.RequestMediaAccessInput, all_fields=True)
class RequestMediaAccessInput:
    """
    Docstring for RequestMediaAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestGeneralMediaAccessInput, all_fields=True)
class RequestGeneralMediaAccessInput:
    """
    Docstring for RequestGeneralMediaAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestBigFileUploadInput, all_fields=True)
class RequestBigFileUploadInput:
    """
    Docstring for RequestMediaUploadInput
    """

    pass


@pydantic.input(model=base_models.FinishBigFileUploadInput, all_fields=True)
class FinishBigFileUploadInput:
    """
    Docstring for FinishMediaUploadInput
    """

    pass


@pydantic.input(model=base_models.RequestBigFileAccessInput, all_fields=True)
class RequestBigFileAccessInput:
    """
    Docstring for RequestBigFileAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestZarrUploadInput, all_fields=True)
class RequestZarrUploadInput:
    """
    Docstring for RequestZarrUploadInput
    """

    pass


@pydantic.input(model=base_models.FinishZarrUploadInput, all_fields=True)
class FinishZarrUploadInput:
    """
    Docstring for FinishZarrUploadInput
    """

    pass


@pydantic.input(model=base_models.RefreshZarrUploadInput, all_fields=True)
class RefreshZarrUploadInput:
    """
    Docstring for RefreshZarrUploadInput
    """

    pass


@pydantic.input(model=base_models.RequestZarrAccessInput, all_fields=True)
class RequestZarrAccessInput:
    """
    Docstring for RequestZarrAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestGeneralZarrAccessInput, all_fields=True)
class RequestGeneralZarrAccessInput:
    """
    Docstring for RequestGeneralZarrAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestSparseUploadInput, all_fields=True)
class RequestSparseUploadInput:
    """
    Docstring for RequestSparseUploadInput
    """

    pass


@pydantic.input(model=base_models.FinishSparseUploadInput, all_fields=True)
class FinishSparseUploadInput:
    """
    Docstring for FinishSparseUploadInput
    """

    pass


@pydantic.input(model=base_models.RefreshSparseUploadInput, all_fields=True)
class RefreshSparseUploadInput:
    """
    Docstring for RefreshSparseUploadInput
    """

    pass


@pydantic.input(model=base_models.RequestSparseAccessInput, all_fields=True)
class RequestSparseAccessInput:
    """
    Docstring for RequestSparseAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestGeneralSparseAccessInput, all_fields=True)
class RequestGeneralSparseAccessInput:
    """
    Docstring for RequestGeneralSparseAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestFabriksUploadInput, all_fields=True)
class RequestFabriksUploadInput:
    """
    Docstring for RequestFabriksUploadInput
    """

    pass


@pydantic.input(model=base_models.FinishFabriksUploadInput, all_fields=True)
class FinishFabriksUploadInput:
    """
    Docstring for FinishFabriksUploadInput
    """

    pass


@pydantic.input(model=base_models.RequestFabriksAccessInput, all_fields=True)
class RequestFabriksAccessInput:
    """
    Docstring for RequestFabriksAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestGeneralFabriksAccessInput, all_fields=True)
class RequestGeneralFabriksAccessInput:
    """
    Docstring for RequestGeneralFabriksAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestKonnektionUploadInput, all_fields=True)
class RequestKonnektionUploadInput:
    """
    Docstring for RequestKonnektionUploadInput
    """

    pass


@pydantic.input(model=base_models.FinishKonnektionUploadInput, all_fields=True)
class FinishKonnektionUploadInput:
    """
    Docstring for FinishKonnektionUploadInput
    """

    pass


@pydantic.input(model=base_models.RequestKonnektionAccessInput, all_fields=True)
class RequestKonnektionAccessInput:
    """
    Docstring for RequestKonnektionAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestGeneralKonnektionAccessInput, all_fields=True)
class RequestGeneralKonnektionAccessInput:
    """
    Docstring for RequestGeneralKonnektionAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestGeneralParquetAccessInput, all_fields=True)
class RequestGeneralParquetAccessInput:
    """
    Docstring for RequestGeneralParquetAccessInput
    """

    pass


@pydantic.input(model=base_models.RequestParquetUploadInput, all_fields=True)
class RequestParquetUploadInput:
    """
    Docstring for RequestParquetUploadInput
    """

    pass


@pydantic.input(model=base_models.FinishParquetUploadInput, all_fields=True)
class FinishParquetUploadInput:
    """
    Docstring for FinishParquetUploadInput
    """

    pass


@pydantic.input(model=base_models.RequestParquetAccessInput, all_fields=True)
class RequestParquetAccessInput:
    """
    Docstring for RequestParquetAccessInput
    """

    pass
