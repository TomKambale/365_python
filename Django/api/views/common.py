"""Shared view plumbing: JSON parsing, pydantic validation, error handling.

The ``api_view`` decorator mirrors FastAPI's behavior: ApiError becomes a
``{"detail": ...}`` JSON response with the matching status code, and pydantic
validation failures become 422s.
"""
import json
import logging
from functools import wraps

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from pydantic import ValidationError

from api.exceptions import ApiError

log = logging.getLogger("azure-admin")


def json_response(data, status: int = 200):
    return JsonResponse(data, status=status, safe=False)


def parse_json_body(request) -> dict:
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        raise ApiError(400, "Invalid JSON body")
    if not isinstance(data, dict):
        raise ApiError(400, "Expected a JSON object")
    return data


def parse_model(model_cls, request):
    """Validate the JSON body against a pydantic model (FastAPI-style 422)."""
    try:
        return model_cls.model_validate(parse_json_body(request))
    except ValidationError as e:
        raise ApiError(422, json.loads(e.json(include_url=False)))


def api_view(*methods: str):
    allowed = {m.upper() for m in methods}

    def decorator(fn):
        @csrf_exempt
        @wraps(fn)
        async def wrapper(request, *args, **kwargs):
            if request.method == "OPTIONS":
                # Preflight is normally answered by django-cors-headers before
                # it reaches the view; this is a safety net.
                return HttpResponse(status=200)
            if request.method not in allowed:
                return JsonResponse({"detail": "Method Not Allowed"}, status=405)
            try:
                return await fn(request, *args, **kwargs)
            except ApiError as e:
                return JsonResponse({"detail": e.detail}, status=e.status_code, safe=False)
            except Exception:
                log.exception(f"Unhandled error on {request.method} {request.path}")
                return JsonResponse({"detail": "Internal Server Error"}, status=500)

        return wrapper

    return decorator
