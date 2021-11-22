
from ..models import MatriseBase
import random
import string
from ..generators.base import BaseGenerator
import uuid

class DefaultPathGenerator(BaseGenerator):
    pass

    def _id_generator(self, size=100, chars=string.ascii_uppercase + string.digits):
        return uuid.uuid4()

    def generatePath(self, model: MatriseBase):
        return f'{self._id_generator()}-sample.{model.group}.{model.name}'