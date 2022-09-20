import graphene


class PandasDType(graphene.Enum):

    OBJECT = "object"
    INT64 = "int64"
    FLOAT64 = "float64"
    BOOL = "bool"
    CATEGORY = "category"
    DATETIME65 = "datetime64"
    TIMEDELTA = "timedelta[ns]"
    UNICODE = "unicode"
