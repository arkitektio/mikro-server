from django.db import models
from koherent.fields import ProvenanceField, HistoricForeignKey
from authentikate.models import Organization

from .folder import File


class MetaSchema(models.Model):
    name = models.CharField(max_length=1000, help_text="The name of the meta schema")
    schema = models.JSONField(help_text="The schema definition in JSON Schema format")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    provenance = ProvenanceField()


class UnstructuredMeta(models.Model):
    file = HistoricForeignKey(File, on_delete=models.CASCADE, related_name="unstructured_metas")
    name = models.CharField(max_length=1000, help_text="The name of the meta data")
    meta = models.JSONField(help_text="The unstructured meta data of the file")
    schema = models.ForeignKey(MetaSchema, on_delete=models.CASCADE, null=True, blank=True)
