from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    """``pg_trgm``: ``embeddings.search.hybrid_search`` ranks lexical hits by trigram similarity."""

    dependencies = [
        ("core", "0011_alter_historicaltabledataset_description_and_more"),
    ]

    operations = [
        TrigramExtension(),
    ]
