from django.contrib import admin
from .models import LokApp, LokUser
# Register your models here.
admin.site.register(LokUser)
admin.site.register(LokApp)