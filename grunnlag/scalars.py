import datetime
from graphene.types import Scalar
from graphql.language import ast


class XArray(Scalar):
    """DateTime Scalar Description"""

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
    """A Representation of a Django File"""

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
    """A Representation of a Django File"""

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
