
from ..models import BordBase
import random
import string
from ..generators.base import BaseGenerator


class DefaultPathGenerator(BaseGenerator):
    pass

    def _id_generator(self, size=6, chars=string.ascii_uppercase + string.digits):
        return ''.join(random.choice(chars) for _ in range(size))

    def generatePath(self, model: BordBase):
        return f'{self._id_generator()}-sample.{model.group}.{model.name}'