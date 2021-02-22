import json
import traceback
from typing import Callable
from uuid import UUID

from channels.db import database_sync_to_async
from dask.callbacks import Callback
from distributed import WorkerPlugin


class TemporaryFile(object):
    def __init__(self, field, tmp="/tmp"):
        self.field = field
        self.tmppath = tmp

    def __enter__(self):
        import os
        import uuid
        _, file_extension = os.path.splitext(self.field.name)
        self.filename = self.tmppath + "/" + str(uuid.uuid4()) + file_extension
        with open(self.filename, 'wb+') as destination:
            for chunk in self.field.chunks():
                destination.write(chunk)

        return self.filename

    def __exit__(self, exc_type, exc_value, tb):
        import os
        os.remove(self.filename)
        if exc_type is not None:
            traceback.print_exception(exc_type, exc_value, tb)
            # return False # uncomment to pass exception through

        return True



# This is necessary so that we serialize the uuid correctly
class UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, UUID):
            # if the obj is uuid, we simply return the value of uuid
            return str(obj)
        return json.JSONEncoder.default(self, obj)
