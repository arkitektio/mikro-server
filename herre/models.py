from django.db import models

# Create your models here.
import django.db.models.options as options
options.DEFAULT_NAMES = options.DEFAULT_NAMES + ('identifiers',)


from django.contrib.auth.models import AbstractUser

class HerreUser(AbstractUser):
    """ A reflection on the real User"""
    roles = models.JSONField(null=True, blank=True)