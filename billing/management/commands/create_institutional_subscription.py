"""Management command to manually provision an institutional platform subscription."""

from django.core.management.base import BaseCommand, CommandError
from organizations.models import Organization
from billing.services import create_institutional_subscription


class Command(BaseCommand):
    help = "Manually provision an institutional platform subscription for an organization."

    def add_arguments(self, parser):
        parser.add_argument("organization_slug", type=str, help="Slug of the organization")

    def handle(self, *args, **options):
        slug = options["organization_slug"]
        try:
            org = Organization.objects.get(slug=slug)
        except Organization.DoesNotExist:
            raise CommandError(f"Organization with slug '{slug}' does not exist.")

        subscription = create_institutional_subscription(organization=org)
        self.stdout.write(
            self.style.SUCCESS(
                f"Successfully provisioned Institutional subscription for '{org.name}' ({org.slug}). Status: {subscription.status}."
            )
        )
