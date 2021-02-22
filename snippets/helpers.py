import numpy as np
from PIL import Image
import xarray as xr
import dask.array as da
import io
import logging

logger = logging.getLogger(__name__)

def array_to_image(array: xr.DataArray, rescale=True, max=True, slicefunction= lambda size: int(size/2)) -> Image :
    
    if "z" in array.dims:
        array = array.max(dim="z") if max else array.sel(z=slicefunction(int(array.sizes["z"])))
    if "t" in array.dims:
        array = array.sel(t=0)

    if "c" in array.dims:
        # Check if we have to convert to monoimage
        if array.c.size == 1:
            array = array.sel(c=0)

            if rescale == True:
                logger.info("Rescaling")
                min, max = array.min(), array.max()
                image = np.interp(array, (min, max), (0, 255)).astype(np.uint8)
            else:
                image = (array * 255).astype(np.uint8)

            from matplotlib import cm
            mapped = cm.viridis(image)

            finalarray = (mapped * 255).astype(np.uint8)

        else:
            if array.c.size >= 3:
                array = array.sel(c=[0,1,2]).data
            elif array.c.size == 2:
                # Two Channel Image will be displayed with a Dark Channel
                array = da.concatenate([array.sel(c=[0,1]).data,da.zeros((array.x.size, array.y.size, 1))], axis=2)

            if rescale == True:
                logger.info("Rescaling")
                min, max = array.min(), array.max()
                image = np.interp(array.compute(), (min, max), (0, 255)).astype(np.uint8)
            else:
                image = (array * 255).astype(np.uint8)

            finalarray = image

    else:
        raise NotImplementedError("Image Does not provide the channel Argument")

    
    img = Image.fromarray(finalarray)
    img = img.convert('RGB')


    return img



