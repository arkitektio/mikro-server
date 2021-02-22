from elements.matrise.storages.base import BaseStorage
from django.core.files.storage import FileSystemStorage


class LocalStorage(FileSystemStorage, BaseStorage):
    pass
