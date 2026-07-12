from rest_framework.response import Response
from rest_framework.views import exception_handler


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

    if response is not None:
        # Extract the existing data (e.g., {"detail": "...", "field": ["error"]})
        data = response.data

        # Wrap into your unified format. Field-level validation errors
        # (e.g. {"name": ["This field is required."]}) have no top-level
        # "detail" key, so fall back to the first available message instead of
        # raising a KeyError.
        detail = _extract_detail(data)

        return Response({"detail": detail}, status=response.status_code)

    return response
