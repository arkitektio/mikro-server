import graphene


class PandasDType(graphene.Enum):

    OBJECT = "object"
    INT64 = "int64"
    FLOAT64 = "float64"
    BOOL = "bool"
    CATEGORY = "categorical"
    DATETIME65 = "datetime64"
    TIMEDELTA = "timedelta[ns]"
    UNICODE = "unicode"
    DATETIME = "datetime"
    DATETIMEZ = "datetimez"
    DATETIMETZ = "datetimetz"
    DATETIME64 = "datetime64[ns]"
    DATETIME64TZ = "datetime64[ns, UTC]"
    DATETIME64NS = "datetime64[ns]"
    DATETIME64NSUTC = "datetime64[ns, UTC]"
    DATETIME64NSZ = "datetime64[ns, UTC]"
    DATETIME64NSZUTC = "datetime64[ns, UTC]"

