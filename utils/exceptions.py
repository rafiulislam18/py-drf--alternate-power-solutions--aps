import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def _extract_detail(data):
    """Pull a human-readable message out of any DRF error payload shape."""
    if isinstance(data, dict):
        if 'detail' in data:
            return data['detail']
        # Field errors: {"name": ["This field is required."]} — take the first.
        for value in data.values():
            if isinstance(value, (list, tuple)) and value:
                return value[0]
            if value:
                return value
        return 'Invalid request.'
    if isinstance(data, (list, tuple)) and data:
        return data[0]
    return data


def custom_exception_handler(exc, context):
    # Get the default DRF error response
    response = exception_handler(exc, context)

    # Identify the view/request for context in the log line.
    view = context.get('view').__class__.__name__ if context.get('view') else 'unknown'
    request = context.get('request')
    path = getattr(request, 'path', 'unknown')

    if response is not None:
        # DRF recognised the exception (validation, auth, 404, throttling, ...).
        # 5xx here is a genuine server error worth a full traceback -> Telegram;
        # 4xx is expected client input, logged at INFO (file only, no Telegram).
        if response.status_code >= 500:
            logger.exception("Server error in %s (%s): %s", view, path, exc)
        else:
            logger.info("Handled API error in %s (%s): %s", view, path, exc)

        # Extract the existing data (e.g., {"detail": "...", "field": ["error"]})
        data = response.data

        # Wrap into your unified format. Field-level validation errors
        # (e.g. {"name": ["This field is required."]}) have no top-level
        # "detail" key, so fall back to the first available message instead of
        # raising a KeyError.
        detail = _extract_detail(data)

        return Response({"detail": detail}, status=response.status_code)

    # response is None -> DRF did not handle it: an unexpected exception that
    # will become a 500. Log the full traceback so it reaches Telegram.
    logger.exception("Unhandled exception in %s (%s): %s", view, path, exc)
    return response
