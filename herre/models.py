from django.db import models

# Create your models here.
import django.db.models.options as options
options.DEFAULT_NAMES = options.DEFAULT_NAMES + ('identifiers',)
