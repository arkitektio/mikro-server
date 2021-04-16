from delt.bridge.auth import Auth
import requests


class BridgeException(Exception):
    pass

class QueryException(Exception):
    pass



class BaseBridge:

    def __init__(self, host, port) -> None:
        self.host = host
        self.port = port
        self.auth = Auth()


    def call(self, query, variables):
        result =  self.auth.post(f"http://{self.host}:{self.port}/graphql", json={
            "query": query,
            "variables": variables
        })
        
        try:
            answer = result.json()
            if "errors" in answer: raise QueryException(str(answer["errors"]))
            return result.json()["data"]
        except KeyError as e:
            raise BridgeException(str(result.json()["errors"]))