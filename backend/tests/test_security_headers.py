"""Phase 11 security headers and single-origin static-serving tests.

Covers the header set on API, static, and error responses; HSTS only on HTTPS;
a strict CSP (hashed inline bootstrap, no unsafe-inline/unsafe-eval) for app
pages; the development-only docs relaxation; and the static frontend rules
(page aliases, SPA-safe navigation fallback, frozen JSON 404 envelope, no
traversal). See docs/SECURITY.md "HTTP security headers".
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from app.config import load_settings
from app.main import create_app, inline_script_hashes

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPO_ROOT / "frontend"

EXPECTED_HEADERS = (
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cross-Origin-Opener-Policy",
    "Cross-Origin-Resource-Policy",
    "X-Permitted-Cross-Domain-Policies",
    "Content-Security-Policy",
)

INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)


def _assert_headers(response, *, https: bool = False) -> str:
    for name in EXPECTED_HEADERS:
        assert response.headers.get(name), f"missing header: {name}"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cross-Origin-Opener-Policy"] == "same-origin"
    # Microphone is same-origin only (voice scheduling); other sensors stay off.
    permissions = response.headers["Permissions-Policy"]
    assert "microphone=(self)" in permissions
    assert "camera=()" in permissions
    assert "geolocation=()" in permissions
    assert response.headers.get("X-Request-Id")
    if https:
        assert response.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    else:
        assert "Strict-Transport-Security" not in response.headers
    return response.headers["Content-Security-Policy"]


async def test_security_headers_on_health_and_api_responses(client) -> None:
    _assert_headers(await client.get("/health/live"))
    _assert_headers(await client.get("/health/ready"))
    _assert_headers(await client.get("/api/v1/auth/me"))


async def test_security_headers_on_static_pages_and_assets(client) -> None:
    _assert_headers(await client.get("/"))
    _assert_headers(await client.get("/index.html"))
    _assert_headers(await client.get("/assets/css/styles.css"))


async def test_static_code_assets_revalidate_but_images_may_cache(client) -> None:
    """Buildless JS/CSS updates must not be stuck behind an hour-long cache."""
    for path in (
        "/index.html",
        "/assets/js/command-menu.js",
        "/assets/css/styles.css",
    ):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert response.headers["Cache-Control"] == "no-cache", path
    image = await client.get("/assets/images/error-404.svg")
    assert image.status_code == 200
    assert image.headers["Cache-Control"] == "public, max-age=3600"


async def test_webp_static_assets_use_the_correct_media_type(client) -> None:
    """WebP must not fall back to octet-stream under X-Content-Type-Options: nosniff."""
    response = await client.get("/assets/images/doctor-background.webp")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/webp"
    _assert_headers(response)


async def test_compressible_static_assets_are_gzipped(client) -> None:
    response = await client.get(
        "/assets/css/styles.css", headers={"Accept-Encoding": "gzip"}
    )
    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in response.headers.get("vary", "").lower()
    # The security headers survive the compression wrapper.
    _assert_headers(response)


async def test_security_headers_on_unhandled_error_responses() -> None:
    import httpx
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/test/boom")
    async def boom() -> dict:
        raise RuntimeError("no leak expected")

    app = create_app(load_settings(env_file=None))
    app.include_router(router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/test/boom")
        assert response.status_code == 500
        _assert_headers(response)


async def test_hsts_is_emitted_only_for_https(client) -> None:
    plain = await client.get("/health/live")
    assert "Strict-Transport-Security" not in plain.headers

    forwarded = await client.get("/health/live", headers={"X-Forwarded-Proto": "https"})
    assert forwarded.headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"

    # Never blindly on localhost even when a proxy header is spoofed on HTTP:
    # the header is only attached when the request itself is HTTPS.
    assert "Strict-Transport-Security" not in (await client.get("/health/live")).headers


async def test_csp_is_strict_for_app_pages(client) -> None:
    for path in ("/", "/index.html", "/assets/js/config.js", "/health/live"):
        csp = _assert_headers(await client.get(path))
        assert "unsafe-inline" not in csp, path
        assert "unsafe-eval" not in csp, path
        assert "frame-ancestors 'none'" in csp, path
        assert "object-src 'none'" in csp, path
        assert "script-src 'self' 'sha256-" in csp, path


async def test_csp_hashes_cover_every_inline_bootstrap_script(client) -> None:
    """The buildless pages keep working under script-src 'self' + hashes."""
    response = await client.get("/index.html")
    csp = response.headers["Content-Security-Policy"]
    for html in sorted(FRONTEND_DIR.rglob("*.html")):
        for match in INLINE_SCRIPT_RE.finditer(html.read_text(encoding="utf-8")):
            digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
            expression = f"'sha256-{base64.b64encode(digest).decode('ascii')}'"
            assert expression in csp, f"{html.name}: inline script not allowed by CSP"
    assert inline_script_hashes(FRONTEND_DIR), "fixture expectation: pages carry the bootstrap"


async def test_docs_csp_relaxation_is_development_only(monkeypatch) -> None:
    import httpx

    dev_app = create_app(load_settings(env_file=None))
    transport = httpx.ASGITransport(app=dev_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        docs = await client.get("/docs")
        assert docs.status_code == 200
        assert "cdn.jsdelivr.net" in docs.headers["Content-Security-Policy"]
        app_page = await client.get("/")
        assert "cdn.jsdelivr.net" not in app_page.headers["Content-Security-Policy"]

    monkeypatch.setenv("API_DOCS_ENABLED", "false")
    prod_app = create_app(load_settings(env_file=None))
    transport = httpx.ASGITransport(app=prod_app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        docs = await client.get("/docs")
        assert docs.status_code == 404
        assert docs.json()["error"]["code"] == "not_found"
        csp = docs.headers["Content-Security-Policy"]
        assert "cdn.jsdelivr.net" not in csp
        assert "unsafe-inline" not in csp


async def test_static_pages_served_with_correct_content_types(client) -> None:
    index = await client.get("/")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert "<title>" in index.text

    script = await client.get("/assets/js/config.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert "DEFAULT_API_BASE_URL" in script.text

    css = await client.get("/assets/css/styles.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")

    # Extension-less aliases resolve to the same pages (/dashboard == /dashboard.html).
    alias = await client.get("/dashboard")
    direct = await client.get("/dashboard.html")
    assert alias.status_code == direct.status_code == 200
    assert alias.text == direct.text


async def test_navigation_fallback_serves_the_custom_404_page(client) -> None:
    """Browser navigations get the themed 404 page with a real 404 status."""
    fallback = await client.get("/some/missing/page", headers={"Accept": "text/html"})
    assert fallback.status_code == 404
    assert fallback.headers["content-type"].startswith("text/html")
    assert "Page not found" in fallback.text
    assert "error-404.svg" in fallback.text
    assert fallback.text == (await client.get("/404.html")).text

    # Unknown paths without an HTML navigation still keep the JSON envelope.
    api_like = await client.get("/some/missing/page")
    assert api_like.status_code == 404
    assert api_like.json()["error"]["code"] == "not_found"


async def test_unknown_paths_keep_the_frozen_json_error_envelope(client) -> None:
    for path, headers in (
        ("/assets/js/missing.js", {}),
        ("/does/not/exist", {}),
        ("/api/v1/definitely-missing", {"Accept": "text/html"}),
        ("/health/definitely-missing", {}),
    ):
        response = await client.get(path, headers=headers)
        assert response.status_code == 404, path
        body = response.json()
        assert set(body) == {"error"}
        assert body["error"]["code"] == "not_found"


async def test_path_traversal_never_escapes_the_frontend_directory(client) -> None:
    for path in ("/%2e%2e/%2e%2e/backend/.env", "/..%2f..%2fbackend%2f.env", "/assets/../../backend/.env"):
        response = await client.get(path)
        assert response.status_code == 404, path
        assert "APP_SECRET" not in response.text
        assert "DATABASE_URL" not in response.text


async def test_static_serving_disabled_when_frontend_is_absent(monkeypatch) -> None:
    import httpx

    monkeypatch.setenv("FRONTEND_DIR", "/nonexistent/frontend")
    app = create_app(load_settings(env_file=None))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        # The API surface is unaffected.
        assert (await client.get("/health/live")).status_code == 200
