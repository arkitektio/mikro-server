import requests


class BridgeException(Exception):
    pass



class BaseBridge:

    def __init__(self, host, port) -> None:
        self.host = host
        self.port = port
    

    def call(self, query, variables, token=None):
        result =  requests.post(f"http://{self.host}:{self.port}/graphql", json={
            "query": query,
            "variables": variables
        }, headers= {"Authorization": f"Bearer {token}"} if token else {})
        
        try:
            return result.json()["data"]
        except KeyError as e:
            raise BridgeException(str(result.json()["errors"]))
        except Exception as e:
            raise BridgeException(str(result.status_code) + str(result.content))