"""Interactive API docs at /docs: Swagger UI from app/static/vendor/swagger-ui/, no CDN and no inline script, so the
page works offline and runs under the editor's CSP (security.py). FastAPI's own /docs and /redoc are off (main.py).

The page sits behind the guard like the API: a browser opens it with the editor's session cookie. The schema says
how the guard takes a key, so «Authorize» in Swagger UI sends it with «Try it out».
"""

from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)

PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rich Studio API</title>
<link rel="stylesheet" href="/static/vendor/swagger-ui/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="/static/vendor/swagger-ui/swagger-ui-bundle.js"></script>
<script src="/static/api-docs.js"></script>
</body>
</html>
"""

# what security.guard accepts; the editor's session cookie is not here: the browser sends it by itself
SECURITY_SCHEMES = {
    "token": {
        "type": "http",
        "scheme": "bearer",
        "description": "Именной токен (`make token`) или STUDIO_API_KEY в заголовке `Authorization: Bearer <токен>`.",
    },
    "api_key": {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Именной токен или STUDIO_API_KEY в заголовке X-API-Key. Браузер после входа в редактор "
        "шлёт cookie сессии сам: ключ здесь нужен, чтобы попробовать запрос от имени другого токена.",
    },
}
# either of them; none while the studio has neither a key nor tokens (open on 127.0.0.1)
SECURITY: list[dict[str, list[str]]] = [{"token": []}, {"api_key": []}, {}]


@router.get("/docs")
def swagger_ui() -> HTMLResponse:
    return HTMLResponse(PAGE)


def describe_auth(app: FastAPI) -> None:
    """app.openapi() also says how to send a key. Only a description: the guard checks it, not FastAPI."""
    build = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = build()
            schema.setdefault("components", {})["securitySchemes"] = SECURITY_SCHEMES
            schema["security"] = SECURITY
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
