"""Load the estimate catalogue + assumptions from the APS workbook: manage.py import_estimate_template <file.xlsx>"""

from django.core.management.base import BaseCommand, CommandError

from apps.solar_estimates.excel_import import WorkbookError, import_workbook


class Command(BaseCommand):
    help = 'Import the equipment catalogue and assumptions from APS_Solar_Estimate_TEMPLATE.xlsx.'

    def add_arguments(self, parser):
        parser.add_argument('file')

    def handle(self, *args, file, **options):
        try:
            result = import_workbook(file)
        except (WorkbookError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Imported {result['catalogue_items']} catalogue items; settings updated: "
            f"{', '.join(result['settings_updated']) or 'none'}."
        ))
