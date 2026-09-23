"""Parse explicit coordinates or a Google Maps share link without arbitrary URL fetching."""

import re
from urllib.parse import urljoin, urlparse

import httpx


ALLOWED_HOSTS = {"maps.app.goo.gl", "maps.google.com", "www.google.com", "google.com"}


def _coordinates_from_url(url: str) -> tuple[float, float] | None:
    for pattern in (r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
                    r"!3d(-?\d+(?:\.\d+)?)[!&]4d(-?\d+(?:\.\d+)?)",
                    r"[?&](?:q|ll)=(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)"):
        match = re.search(pattern, url)
        if match:
            return float(match.group(1)), float(match.group(2))
    return None


def coordinates_from_maps_url(url: str) -> tuple[float, float]:
    current = url
    with httpx.Client(timeout=10, follow_redirects=False) as client:
        for _ in range(6):
            parsed = urlparse(current)
            if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
                raise ValueError("Only HTTPS Google Maps links are accepted")
            coordinates = _coordinates_from_url(current)
            if coordinates:
                return coordinates
            response = client.get(current)
            if response.status_code not in (301, 302, 303, 307, 308):
                break
            current = urljoin(current, response.headers["location"])
    raise ValueError("Could not resolve coordinates from Google Maps link; enter latitude and longitude")
