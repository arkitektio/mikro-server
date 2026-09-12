from django.contrib import admin

# Register your models here.
from datalayer import models


admin.site.register(models.DatalayerStore)
admin.site.register(models.MediaStore)
admin.site.register(models.BigFileStore)
admin.site.register(models.ZarrStore)
admin.site.register(models.ParquetStore)
admin.site.register(models.FabriksStore)
