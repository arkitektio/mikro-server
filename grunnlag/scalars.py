import datetime
from graphene.types import Scalar
from graphene.types.generic import GenericScalar
from graphql.language import ast
from balder.types.scalars import Upload


class ModelFile(Upload):
    """A Model File"""


class ModelData(Scalar):
    """ A model"""

    @staticmethod
    def serialize(dt):
        return dt

    @staticmethod
    def parse_literal(node):
        if isinstance(node, ast.StringValue):
            return node.value

    @staticmethod
    def parse_value(value):
        return value


class AffineMatrix(Scalar):
    """ A model"""

    @staticmethod
    def serialize(dt):
        return dt

    @staticmethod
    def parse_literal(node):
        if isinstance(node, ast.StringValue):
            return node.value

    @staticmethod
    def parse_value(value):
        return value



class XArrayInput(Scalar):
    """XArray scalar
    
    This scalar is used to represent xarray objects and allows them to
    be serialized correct.
    XArray objects are used to represent multidimensional data, such as
    time series, images, and other data.

    They are used in this project to represent the data of a Representation.
    The Python interface can used to extra and manipulate the data.

    This scalar is used to represent the data stored as an object on a S3 bucket
    this object representats a zarr store of an xarray
    
    """

    @staticmethod
    def serialize(dt):
        return dt

    @staticmethod
    def parse_literal(node):
        if isinstance(node, ast.StringValue):
            return node.value

    @staticmethod
    def parse_value(value):
        return value


class File(Scalar):
    """A Representation of a Django File
    """

    @staticmethod
    def serialize(dt):
        return dt

    @staticmethod
    def parse_literal(node):
        if isinstance(node, ast.StringValue):
            return node.value

    @staticmethod
    def parse_value(value):
        return value


class Store(Scalar):
    """Store
    
    This scalar is used to represent zarr store objects and allows them to
    be serialized correct.
    Zarr Stores are used to retrieve multidimensional data, such as
    time series, images, and other data.

    This scalar is used to represent the data stored as an object on a S3 bucket
    this object representats a zarr store of an xarray
    
    """


    @staticmethod
    def serialize(dt):
        return dt

    @staticmethod
    def parse_literal(node):
        if isinstance(node, ast.StringValue):
            return node.value

    @staticmethod
    def parse_value(value):
        return value


class Parquet(Scalar):
    """A Parquet file
    
    This scalar is used to represent parquet files and allows them to be accesed trough
    the code generator python libraries with the correct type.
    
    On the python side this will be converted from and to a pandas dataframe."""

    @staticmethod
    def serialize(dt):
        return dt

    @staticmethod
    def parse_literal(node):
        if isinstance(node, ast.StringValue):
            return node.value

    @staticmethod
    def parse_value(value):
        return value


class MetricValue(GenericScalar):
    """A Metric Value
    
    This scalar ensures serializaiton of metric values. Metric values
    can be of different types, such as int, float, string, datetime, etc.

    However we impose a few rules on the metric values:
    - The value must be a scalar
    - The value must be a scalar that can be serialized to JSON

    
    """


class FeatureValue(GenericScalar):
    """A Feature Value
    
    This scalar ensures serializaiton of feature values. Feature values
    can be of different types, such as int, float, string, datetime, etc.

    However we impose a few rules on the metric values:
    - The value must be a scalar
    - The value must be a scalar that can be serialized to JSON
    
    
    """