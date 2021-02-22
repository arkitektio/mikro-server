from django.utils.functional import LazyObject
from .generators.base import BaseGenerator
from django.conf import settings
from django.utils.module_loading import import_string
from storages.base import BaseStorage

def get_default_bord_storage(import_path=None):
    return import_string(import_path or settings.BORD["STORAGE_CLASS"])


class Storage(LazyObject):
    def _setup(self):
        self._wrapped = get_default_bord_storage()()


default_zarr_storage = Storage()


def get_default_zarr_generator(import_path=None):
    return import_string(import_path or settings.BORD["GENERATOR_CLASS"])


class Generator(LazyObject):
    def _setup(self, *args, **kwargs):
        self._wrapped = get_default_zarr_generator()(*args, **kwargs)


class BordSettings():

    S3_ENDPOINT_URL = settings.AWS_S3_ENDPOINT_URL #Non Public
    S3_PUBLIC_URL = settings.BORD["PUBLIC_URL"]

    API_VERSION = settings.BORD["API_VERSION"]
    FILE_VERSION = settings.BORD["FILE_VERSION"]
    STORAGE_CLASS = settings.BORD["STORAGE_CLASS"]
    GENERATOR_CLASS = settings.BORD["GENERATOR_CLASS"]
    STORAGE_BUCKET = settings.BORD["BUCKET"]


    def getStorageClass(self, import_path=None) -> BaseStorage:
        return Storage()

    def getPathGeneratorClass(self, import_path=None) -> BaseGenerator:
        return Generator()



bord_settings = BordSettings()


def get_active_settings():
    return bord_settings