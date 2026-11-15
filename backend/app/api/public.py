"""Public-site metadata endpoints: robots.txt and sitemap.xml.

These are intentionally generated from ``PUBLIC_ORIGIN`` instead of being
hard-coded, because the repository does not contain a verified production
domain. The deployment owner sets ``PUBLIC_ORIGIN`` to the canonical public
origin; the generated sitemap then uses absolute URLs for only the public
pages. Authenticated clinical pages are never listed.

robots.txt is a crawling hint, not an access control. Every private page and
API remains protected by server-side authentication and authorization.
"""
from __future__ import annotations

from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

router = APIRouter(tags=["public-site"], include_in_schema=False)

# Only these public pages belong in the sitemap. Keep this list in step with
# the public pages that carry `index, follow` metadata.
PUBLIC_PAGE_PATHS: tuple[str, ...] = ("/", "/privacy.html", "/terms.html")

_ROBOTS_CACHE = "public, max-age=3600"
_SITEMAP_CACHE = "public, max-age=3600"


def _origin(request: Request) -> str:
    return str(request.app.state.settings.public_origin).rstrip("/")


def render_robots(origin: str) -> str:
    """Return a whitelist robots.txt for the public site.

    The whitelist deliberately avoids enumerating authenticated routes. The
    rule set allows only the public landing/legal pages and local assets;
    everything else is disallowed. Longer Allow rules take precedence over the
    broad Disallow rule for crawlers that implement the standard matching
    algorithm.
    """
    return "\n".join(
        [
            "User-agent: *",
            "Allow: /$",
            "Allow: /privacy.html",
            "Allow: /terms.html",
            "Allow: /assets/",
            "Allow: /sitemap.xml",
            "Allow: /robots.txt",
            "Disallow: /",
            "",
            "# robots.txt is not an access control. Authenticated data is protected",
            "# by server-side authentication and authorization.",
            f"Sitemap: {origin}/sitemap.xml",
            "",
        ]
    )


def render_sitemap(origin: str) -> str:
    """Return a minimal sitemap containing only public pages."""
    urls = "".join(
        f"  <url><loc>{escape(origin + path)}</loc></url>\n"
        for path in PUBLIC_PAGE_PATHS
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}"
        "</urlset>\n"
    )


@router.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        render_robots(_origin(request)),
        headers={"Cache-Control": _ROBOTS_CACHE},
    )


@router.get("/sitemap.xml")
async def sitemap_xml(request: Request) -> Response:
    return Response(
        render_sitemap(_origin(request)),
        media_type="application/xml",
        headers={"Cache-Control": _SITEMAP_CACHE},
    )
