from enum import Enum

import strawberry


# ---- Enums (shared by models & GraphQL) ----
@strawberry.enum
class ChannelKind(str, Enum):
    FREE_SPACE = "FREE_SPACE"
    FIBER_SM = "FIBER_SM"
    FIBER_MM = "FIBER_MM"
    WAVEGUIDE = "WAVEGUIDE"


@strawberry.enum
class PortRole(str, Enum):
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"


@strawberry.enum
class ElementKind(str, Enum):
    LASER = "LASER"
    PINHOLE = "PINHOLE"
    LAMP = "LAMP"
    OTHER_SOURCE = "OTHER_SOURCE"
    DETECTOR = "DETECTOR"
    CCD = "CCD"
    MIRROR = "MIRROR"
    BEAM_SPLITTER = "BEAM_SPLITTER"
    LENS = "LENS"
    OBJECTIVE = "OBJECTIVE"
    FILTER = "FILTER"
    POLARIZER = "POLARIZER"
    WAVEPLATE = "WAVEPLATE"
    APERTURE = "APERTURE"
    SHUTTER = "SHUTTER"
    SAMPLE = "SAMPLE"
    OTHER = "OTHER"


@strawberry.enum
class PulseKind(str, Enum):
    CW = "CW"
    SINGLE = "Single"
    QSWITCHED = "QSwitched"
    REPETITIVE = "Repetitive"
    MODE_LOCKED = "ModeLocked"
    OTHER = "Other"


@strawberry.enum
class ObjectiveCorrectionKind(str, Enum):
    UV = "UV"
    PLAN_APO = "PlanApo"
    PLAN_FLUOR = "PlanFluor"
    SUPER_FLUOR = "SuperFluor"
    VIOLET_CORRECTED = "VioletCorrected"
    ACHRO = "Achro"
    ACHROMAT = "Achromat"
    FLUOR = "Fluor"
    FL = "Fl"
    FLUAR = "Fluar"
    NEOFLUAR = "Neofluar"
    FLUOTAR = "Fluotar"
    APO = "Apo"
    PLAN_NEOFLUAR = "PlanNeofluar"
    OTHER = "Other"


@strawberry.enum
class ObjectiveImmersion(str, Enum):
    OIL = "Oil"
    WATER = "Water"
    WATER_DIPPING = "WaterDipping"
    AIR = "Air"
    MULTI = "Multi"
    GLYCEROL = "Glycerol"
    OTHER = "Other"


@strawberry.enum
class FilterKind(str, Enum):
    DICHROIC = "Dichroic"
    LONG_PASS = "LongPass"
    SHORT_PASS = "ShortPass"
    BAND_PASS = "BandPass"
    MULTI_PASS = "MultiPass"
    NEUTRAL_DENSITY = "NeutralDensity"
    TUNEABLE = "Tuneable"
    OTHER = "Other"
