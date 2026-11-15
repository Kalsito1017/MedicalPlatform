"""Public-site metadata, robots, sitemap, and legal-page serving tests."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from app.config import load_settings

NOINDEX_PATHS = (
    "/dashboard.html",
    "/patients.html",
    "/patient.html",
    "/appointments.html",
    "/messages.html",
    "/ai.html",
    "/analytics.html",
    "/admin.html",
    "/system-logs.html",
    "/404.html",
)

PUBLIC_PATHS = ("/", "/privacy.html", "/terms.html")


def _origin() -> str:
    return load_settings(env_file=None).public_origin.rstrip("/")


async def test_robots_txt_is_a_public_whitelist(client) -> None:
    response = await client.get("/robots.txt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["Cache-Control"] == "public, max-age=3600"
    text = response.text
    assert "User-agent: *" in text
    assert "Allow: /$" in text
    assert "Allow: /privacy.html" in text
    assert "Allow: /terms.html" in text
    assert "Allow: /assets/" in text
    assert "Allow: /sitemap.xml" in text
    assert "Allow: /robots.txt" in text
    assert "Disallow: /" in text
    assert f"Sitemap: {_origin()}/sitemap.xml" in text
    # Do not enumerate the authenticated route structure in robots.txt.
    for name in ("dashboard", "patients", "appointments", "messages", "admin", "analytics"):
        assert name not in text.lower()


async def test_sitemap_xml_contains_only_public_pages(client) -> None:
    response = await client.get("/sitemap.xml")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert response.headers["Cache-Control"] == "public, max-age=3600"

    root = ET.fromstring(response.text)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [node.text for node in root.findall("sm:url/sm:loc", ns)]
    origin = _origin()
    assert locs == [f"{origin}/", f"{origin}/privacy.html", f"{origin}/terms.html"]
    for url in locs:
        assert "/api/" not in url
        assert "dashboard" not in url
        assert "patient" not in url
        assert "appointment" not in url
        assert "admin" not in url
        assert "system-logs" not in url


async def test_authenticated_pages_are_noindex(client) -> None:
    for path in NOINDEX_PATHS:
        response = await client.get(path)
        assert response.status_code == 200, path
        assert response.headers.get("X-Robots-Tag") == "noindex, nofollow", path
        assert 'name="robots" content="noindex, nofollow"' in response.text, path


async def test_public_pages_are_indexable(client) -> None:
    for path in PUBLIC_PATHS:
        response = await client.get(path)
        assert response.status_code == 200, path
        assert 'name="robots" content="index, follow"' in response.text, path
        assert "noindex" not in response.headers.get("X-Robots-Tag", ""), path


async def test_favicon_manifest_and_og_assets_are_served(client) -> None:
    for path, content_type in (
        ("/favicon.ico", "image/"),
        ("/apple-touch-icon.png", "image/png"),
        ("/assets/images/favicon.svg", "image/svg+xml"),
        ("/assets/images/favicon-16x16.png", "image/png"),
        ("/assets/images/favicon-32x32.png", "image/png"),
        ("/assets/images/favicon-192x192.png", "image/png"),
        ("/assets/images/favicon-512x512.png", "image/png"),
        ("/assets/images/og-medicore.png", "image/png"),
        ("/site.webmanifest", "application/manifest+json"),
    ):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith(content_type), path


async def test_legal_pages_are_public_and_have_review_placeholders(client) -> None:
    for path in ("/privacy.html", "/terms.html"):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith("text/html"), path
        assert "MediCore" in response.text
        assert "legal-placeholder" in response.text
        assert "legal counsel" in response.text
        assert "mailto:" not in response.text


async def test_api_and_health_responses_are_noindex(client) -> None:
    for path in ("/api/v1/auth/me", "/health/live", "/health/ready"):
        response = await client.get(path)
        assert response.headers.get("X-Robots-Tag") == "noindex, nofollow", path
