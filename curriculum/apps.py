from django.apps import AppConfig


class CurriculumConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "curriculum"

    def ready(self):
        # Registers the placement-audio cleanup receivers. Importing here is
        # what also takes PlacementResult off the fast-delete path, so cleanup
        # fires for cascaded deletes too (see signals.py).
        from . import signals  # noqa: F401
