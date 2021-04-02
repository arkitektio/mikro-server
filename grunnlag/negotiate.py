

def on_negotiate(request):

    return {
        "protocol": "s3",
        "path": "p-tnagerl-lab1:9000",
        "params": {
            "access_key": "weak_access_key",
            "secret_key": "weak_secret_key"
        }
    }