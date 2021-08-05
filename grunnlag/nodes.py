from grunnlag.models import Experiment, Representation, Sample, Thumbnail
from delt.registry.nodes import registry


@registry.func()
async def show(rep: Representation) -> Representation:
    """Show

    Shows an image on an Application

    Args:
        rep (Representation): The to be showed representation

    Returns:
        Representation: The Output Representatoin
    """
    return None


@registry.func()
async def create_sample(name: str, exp: Experiment = None) -> Sample:
    """Create Sample

    Creates a Sample on Elements

    Args:
        name (str): The name of the Sample
        experiment (Experiment, optional): The experiment this Sample belongs to, defaults to None

    Returns:
        Sample: The created Sample
    """
    return None



@registry.func()
async def acquire(sample: Sample = None, name: str = "Name", iterations: int = 6) -> Representation:
    """Acquire

    Acquires an Image without any Parameters specified (the implementing
    Microscope may use its own Settings at the given Moment)

    Args:
        sample (Sample, optional): Where to put the Sample In. Defaults to None.
        name (str, optional): The Prepended Name for the Representation. Defaults to "Name".
        iterations (int, optional): How Many Iterations should we undergo?. Defaults to 6.

    Returns:
        Representation: The Output Representatoin
    """
    return None


@registry.func()
async def stream(sample: Sample = None, name: str = "Name", iterations: int = 6) -> Representation:
    """Stream

    Streams Images without any Parameters specified (the implementing
    Microscope may use its own Settings at the given Moment)

    Args:
        sample (Sample, optional): Where to put the Sample In. Defaults to None.
        name (str, optional): The Prepended Name for the Representation. Defaults to "Name".
        iterations (int, optional): How Many Iterations should we undergo?. Defaults to 6.

    Returns:
        Representation: The Representation that is created
    """
    yield None


@registry.func()
async def upload_active(name: str = None, sample: Sample = None) -> Representation:
    """Upload Active

    Uploads the currently active Image in the Application to Elements

    Args:
        name (str, optional): The name of the representation
        sample (Sample, optional): The Sample that we are going to put the Representation in

    Returns:
        Representation: The Output Representatoin
    """
    return None



@registry.func()
async def gaussian_blur(rep: Representation, sigma: int = 5) -> Representation:
    """Gaussian Blur

    Takes a {rep} and convolves it with a Gaussian Blur of standard deviation {sigma}

    Args:
        rep (Representation): The to be showed representation
        sigma (int, optional): The standard deviation for the gaussian-blur. Defaults to 5.

    Returns:
        Representation: The Output Representatoin
    """
    return None


@registry.func()
async def maxisp(rep: Representation, dim: str = "z") -> Representation:
    """Maximum Intensity Projection

    Takes a {rep} and projects it on its {dim}. Dimension choices are x y c z t

    Args:
        rep (Representation): The to be showed Stack
        dim (str, optional): The dimension to project. Defaults to "z"

    Returns:
        Representation: The projected Stack
    """
    return None


@registry.func()
async def generate_thumbnail(rep: Representation) -> Representation:
    """Generate Thumbnail

    Takes a {rep} and generates a thumbnail

    Args:
        rep (Representation): The to be showed representation

    Returns:
        Thumbnail: The Output Representatoin
    """
    return None