from django.contrib import admin
from .models import Representation, Sample, Experiment
# Register your models here.
admin.site.register(Representation)
admin.site.register(Sample)
admin.site.register(Experiment)