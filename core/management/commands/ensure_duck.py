from django.core.management.base import BaseCommand
from datalayer.duck import DuckLayer


class Command(BaseCommand):
    help = "Creates all configured apps or overwrites them"

    def handle(self, *args, **options):

        d = DuckLayer()
        d.connection.install_extension("httpfs")
