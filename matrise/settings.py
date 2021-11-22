from django.utils.functional import LazyObject
from .generators.base import BaseGenerator
from django.conf import settings
from django.utils.module_loading import import_string
from storages.base import BaseStorage


def get_default_zarr_storage(import_path=None):
    return import_string(import_path or settings.MATRISE["STORAGE_CLASS"])


class Storage(LazyObject):
    def _setup(self):
        self._wrapped = get_default_zarr_storage()()


default_zarr_storage = Storage()


def get_default_zarr_generator(import_path=None):
    return import_string(import_path or settings.MATRISE["GENERATOR_CLASS"])


class Generator(LazyObject):
    def _setup(self, *args, **kwargs):
        self._wrapped = get_default_zarr_generator()(*args, **kwargs)


class MatriseSettings():

    S3_ENDPOINT_URL = settings.MATRISE["PRIVATE_URL"] #Non Public
    S3_PUBLIC_URL = settings.MATRISE["PUBLIC_URL"]

    ACCESS_KEY = settings.MATRISE["ACCESS_KEY"]
    SECRET_KEY = settings.MATRISE["SECRET_KEY"]


    API_VERSION = settings.MATRISE["API_VERSION"]
    FILE_VERSION = settings.MATRISE["FILE_VERSION"]
    STORAGE_CLASS = settings.MATRISE["STORAGE_CLASS"]
    GENERATOR_CLASS = settings.MATRISE["GENERATOR_CLASS"]
    STORAGE_BUCKET = settings.MATRISE["BUCKET"]


    def getStorageClass(self, import_path=None) -> BaseStorage:
        return Storage()

    def getPathGeneratorClass(self, import_path=None) -> BaseGenerator:
        return Generator()








matrise_settings = MatriseSettings()


def get_active_settings():
    return matrise_settings