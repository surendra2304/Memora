"""Small ASGI middleware for a hard ceiling on inbound HTTP request bodies."""

from fastapi import HTTPException, status
from starlette.responses import JSONResponse


class RequestBodyLimitMiddleware:
    """Reject bodies larger than ``max_body_bytes`` before route processing.

    Model-level limits are still important for field-specific semantics, but they
    run only after the ASGI server and JSON parser have already buffered/decoded
    a request. This middleware caps the bytes consumed by the application, also
    for streaming/chunked requests that omit ``Content-Length``.
    """

    def __init__(self, app, max_body_bytes: int = 1_048_576):
        if max_body_bytes < 1:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except (TypeError, ValueError):
                declared_size = None
            if declared_size is not None and declared_size > self.max_body_bytes:
                response = JSONResponse(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    content={
                        "detail": (
                            "Request body exceeds the "
                            f"{self.max_body_bytes}-byte limit."
                        )
                    },
                )
                await response(scope, receive, send)
                return

        received_size = 0

        async def limited_receive():
            nonlocal received_size
            message = await receive()
            if message.get("type") == "http.request":
                received_size += len(message.get("body", b""))
                if received_size > self.max_body_bytes:
                    # Raised while FastAPI is awaiting the body, before it can
                    # invoke the route. Starlette's HTTPException handler maps
                    # this to the same 413 response as the Content-Length fast
                    # path above.
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=(
                            "Request body exceeds the "
                            f"{self.max_body_bytes}-byte limit."
                        ),
                    )
            return message

        await self.app(scope, limited_receive, send)
