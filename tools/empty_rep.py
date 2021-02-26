
import os
import sys

import dask.array as da
import django
import xarray as xr


def main():
    from grunnlag.models import Representation, Sample
    samp, created = Sample.objects.get_or_create(name="TestSample",creator_id=1)

    array = xr.DataArray(da.zeros((1024,1024,3,3,1)), dims=["x","y","c","z","t"])

    Representation.objects.from_xarray(array, sample=samp, name="maxisp")



if __name__ == "__main__":
    sys.path.insert(0, '/workspace')
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "elements.settings")
    django.setup()

    main()