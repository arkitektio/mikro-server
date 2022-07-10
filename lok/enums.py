from django.db.models import TextChoices

class LokGrantType(TextChoices):
    CLIENT_CREDENTIALS = "client-credentials", "Backend (Client Credentials)"
    IMPLICIT = "implicit", "Implicit Grant"
    AUTHORIZATION_CODE = "authorization-code", "Authorization Code"
    PASSWORD = "password", "Password"

    SESSION = "session", "Django Session"
