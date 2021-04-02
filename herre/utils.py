import logging
import jwt
import json
import base64
from .settings import get_active_settings
import re
from django.core.exceptions import ImproperlyConfigured, PermissionDenied

active_settings = get_active_settings()


logger = logging.getLogger(__name__)
jwt_re = re.compile(r'Bearer\s(?P<token>[^\s]*)')

def decode_token(token):
    try:
        headers_enc, payload_enc, verify_signature = token.split(".")
    except ValueError:
        raise jwt.InvalidTokenError(f"Token does not conform to standard JWT {token}")

    payload_enc += '=' * (-len(payload_enc) % 4)  # add padding
    payload = json.loads(base64.b64decode(payload_enc).decode("utf-8"))
    print(payload)
    algorithms = [active_settings.key_type]
    public_key = active_settings.public_key
    if not public_key:
        raise ImproperlyConfigured('Missing setting HERRE PUBLIC_KEY')
    
    decoded = jwt.decode(token, public_key, algorithms=algorithms)
    return decoded



def token_from_authorization(authorization):
    m = jwt_re.match(authorization)
    if m:
        token = m.group("token")
        return decode_token(token), token
    else:
        logger.error("Not a valid token. Skipping!")
        return False, False


def check_token_from_request(request):
    

    if request.META.get("HTTP_AUTHORIZATION", "").startswith("Bearer"):
            if not hasattr(request, "user") or request.user.is_anonymous:
                return token_from_authorization(request.META.get("HTTP_AUTHORIZATION"))
    
    return False, False
