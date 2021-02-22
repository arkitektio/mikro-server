from abc import ABC, abstractmethod

class BaseGenerator(ABC):

    @abstractmethod
    def generatePath(self, model):
        raise NotImplementedError("Abstract")