"""Celery task for the weekly EnergiTrack solar report (Mondays 01:00 SAST)."""

import logging

from celery import shared_task

from .weekly_report import (
    SiteFetchFailed,
    WeeklyReportError,
    build_weekly_report,
    send_report_alert,
    send_report_ready_email,
)

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=600)
def create_weekly_solar_report(self):
    """Save the Urban Growth report for the week that just ended, from EnergiTrack.

    If a site fails, retry (every 10 min, 3 times) before saving
    that site with empty values on the last attempt. After a save, one
    "report ready" email goes to APS + the client; warnings (if any) go to APS
    only, as a separate email + Telegram alert.
    """
    last_attempt = self.request.retries >= self.max_retries
    try:
        result = build_weekly_report(allow_failures=last_attempt)
    except SiteFetchFailed as exc:
        logger.warning("Weekly solar report: site(s) failed, will retry: %s", exc)
        raise self.retry(exc=exc)
    except WeeklyReportError as exc:
        logger.error("Weekly solar report not created: %s", exc)
        send_report_alert(error=exc)
        return f"failed: {exc}"

    summary = result.summary()
    if result.report is not None:
        send_report_ready_email(result)  # APS + client, every successful save
    if result.warnings:
        logger.warning("%s %s", summary, ' | '.join(result.warnings))
        send_report_alert(result)
    else:
        logger.info(summary)
    return summary
