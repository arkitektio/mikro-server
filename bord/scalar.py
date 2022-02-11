from graphql.language import ast
from graphene import Scalar


class DataFrame(Scalar):
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
