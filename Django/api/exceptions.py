from typing import Any


class ApiError(Exception):
    """Django-side equivalent of FastAPI's HTTPException.

    ``detail`` may be a string or a dict; it is returned to the client as
    ``{"detail": <detail>}`` to match the FastAPI response shape.
    """

    def __init__(self, status_code: int, detail: Any):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"{status_code}: {detail}")
