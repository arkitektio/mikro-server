from django.apps import AppConfig


class KanneServerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "kanne_server"

    def ready(self) -> None:
        # Teach strawberry_django to treat ``QuantityField`` (a BigIntegerField
        # subclass that stores a canonical integer) as an ``int`` wherever a field
        # is declared ``auto`` / ``fields="__all__"`` — e.g. filter types.
        # strawberry_django matches Django field classes exactly (``type(field)``),
        # not by MRO, so the custom subclass has to be registered explicitly.
        # Output/input types that want the dimension-typed wire scalar still
        # annotate ``kanne_server.scalars.Length`` / ``Duration`` directly, which
        # overrides this default.
        try:
            from strawberry_django.fields.types import (
                field_type_map,
                input_field_type_map,
            )
        except Exception:  # pragma: no cover - strawberry_django not installed
            return

        from .fields import QuantityField

        field_type_map.setdefault(QuantityField, int)
        input_field_type_map.setdefault(QuantityField, int)
