from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "scheduling"

    def ready(self):
        # Registers the cohort seat-cap receiver (Phase 4). An M2M write never
        # reaches save()/clean(), so this is the only place a raw
        # students.add() can be stopped from overfilling a class — see signals.py.
        from . import signals  # noqa: F401
