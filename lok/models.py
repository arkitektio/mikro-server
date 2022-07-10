from .enums import LokGrantType
from django.db import models# Create your models here.
from django.contrib.auth.models import AbstractUser

class LokUser(AbstractUser):
    """ A reflection on the real User"""
    email = models.EmailField(unique=True)
    roles = models.JSONField(null=True, blank=True)

class LokApp(models.Model):
    client_id = models.CharField(unique=True, max_length=2000)
    name = models.CharField(max_length=2000)
    grant_type = models.CharField(choices=LokGrantType.choices, max_length=2000)


    def __str__(self):
        return f'{self.name}'