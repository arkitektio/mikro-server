
from django.conf import settings
from django.utils.functional import LazyObject
from django.utils.module_loading import import_string

def get_default_parquet_storage(import_path=None):
    return import_string(import_path or settings.DEFAULT_PARQUET_STORAGE)


class DefaultParquetStorage(LazyObject):
    def _setup(self):
        self._wrapped = get_default_parquet_storage()()


default_parquet_storage = DefaultParquetStorage()


def get_default_parquet_generator(import_path=None):
    return import_string(import_path or settings.DEFAULT_PARQUET_GENERATOR or settings.DEFAULT_NAME_GENERATOR)


class DefaultParquetGenerator(LazyObject):
    def _setup(self, *args, **kwargs):
        self._wrapped = get_default_parquet_generator()(*args, **kwargs)


default_parquet_generator = DefaultParquetGenerator
