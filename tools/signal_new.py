
import os
import sys

import dask.array as da
import django
import xarray as xr


def main():
    from grunnlag.subscriptions import MyNewestRep
    from grunnlag.models import Representation
    MyNewestRep.broadcast(Representation.objects.get(id=1), groups=["newest_rep_1"])



if __name__ == "__main__":
    sys.path.insert(0, '/workspace')
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "elements.settings")
    django.setup()

    main()