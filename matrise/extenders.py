import pandas as pd
import xarray as xr
import dask.dataframe as dd
from django.conf import settings
import logging

arnheim_debug = settings.DEBUG
logger = logging.getLogger(__name__)


logger.info("Making DataArrays Look Beautiful")
xr.set_options(display_style="html")
logger.info("Extending DataArray")



class ArnheimError(Exception):
    pass


@xr.register_dataarray_accessor("biometa")
class MetaAccessor:
    def __init__(self, xarray_obj):
        self._obj = xarray_obj
        self._channelvalue = None
        self._channeldict = None
        self._planesdict = None
        self._planes = None

    def selchannel(self, **kwargs):
        """Return the geographic center point of this dataset."""
        # we can use a cache on our accessor objects, because accessors
        # themselves are cached on instances that access them.

        lala = pd.DataFrame(self._obj.channel.data.compute())
        for key, value in kwargs.items():
            lala = lala[lala[key] == value]

        return lala

    @property
    def name(self):
        return self._obj.attrs["seriesname"]

    @property
    def scan(self):
        return pd.DataFrame(self._obj.attrs["scan"])

    @property
    def channels(self):
        if not "channels" in self._obj.coords:
            raise ArnheimError("No channels. Did you transform the Array in (c) with coordinate changes?")
        """Return the geographic center point of this dataset."""
        # we can use a cache on our accessor objects, because accessors
        # themselves are cached on instances that access them.
        lala = dd.from_dask_array(self._obj.channels.data)
        return lala

    @property
    def planes(self):
        if not "planes" in self._obj.coords:
            raise ArnheimError("No planes. Did you transform the Array in (c,z,t) with coordinate changes?")
        """Return the geographic center point of this dataset."""
        # we can use a cache on our accessor objects, because accessors
        # themselves are cached on instances that access them.

        lala = dd.from_dask_array(self._obj.planes.data.flatten())
        return lala

    @property
    def savecoords(self):
        ''' All the save coordinates for accessing'''
        return [key for key, value in self._obj.coords.items()]


@xr.register_dataarray_accessor("viewer")
class MetaAccessor:
    def __init__(self, xarray_obj):
        self._obj = xarray_obj
        self.log = logger.info

    def show(self, maxisp=True, t=0, rgb=(0, 1, 2), figsize=None, scale=12):
        import matplotlib.pyplot as plt

        figsize = (scale + scale/4, scale * (self._obj.shape[0]/self._obj.shape[1]))
        if figsize:
            figsize = figsize

        fig = plt.figure(figsize=figsize)
        ax = fig.add_subplot(111)


        image = self._obj
        if "t" in image.dims:
            self.log(f"Stack has {len(image.t)} Timepoints: selecting t={t}")
            image = image.sel(t=t)
        if "z" in image.dims and maxisp:
            self.log(f"Stack has {len(image.z)} Z Planes: Projecting maximum intensity")
            image = image.max(dim="z")

        if "c" in image.dims:
            nchannels = len(image.c)
            if nchannels == 1:
                channelname = str(image.channels.data["Name"].compute())
                image = image.sel(c=image.c[0])
                plot = image.plot.imshow(ax= ax)
            elif nchannels == 2:
                self.log(f"Stack has 2 Channels: Merging intensity")
                channelname = "Merged " + " & ".join(image.channels.data["Name"].compute())
                image = image.max(dim="c")
                plot = image.plot.imshow(ax= ax)
            elif nchannels == 3:
                channelname = " & ".join(image.channels.data["Name"].compute())
                plot = image.plot.imshow(ax= ax, rgb="c")
            elif nchannels > 3:
                image = image.sel(c=rgb)
                channelname = " & ".join(image.channels.data["Name"].compute())
                plot = image.plot.imshow(rgb="c",ax= ax)
        else:
            channelname = "Not Set"
            plot = image.plot.imshow(ax= ax)

        ax.set_title(channelname)
        return fig

EXTENDED = True