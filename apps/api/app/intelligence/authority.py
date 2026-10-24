"""Source authority from the page and the domain, never from a model's adjective."""

from __future__ import annotations

from urllib.parse import urlparse

PUBLIC_SECTOR_SUFFIXES = (".nhs.uk", ".gov.uk", ".ac.uk", ".edu.au", ".gov.au", ".gc.ca", ".gov", ".edu", ".mil")
COMMUNITY_HOSTS = ("reddit.com", "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com", "youtube.com")
REPUTABLE = ("wikipedia.org", "bbc.co.uk", "bbc.com", "theguardian.com", "reuters.com", "apnews.com")
MARKETPLACE = ("ticketmaster", "stubhub", "viagogo", "seatgeek", "ebay", "amazon.", "marketplace", "reseller")

CLASSES = (
    "PRIMARY_OFFICIAL",
    "PRIMARY_PUBLIC_BODY",
    "PRIMARY_DOCUMENT",
    "REPUTABLE_SECONDARY",
    "MARKETPLACE",
    "COMMUNITY",
    "UNKNOWN",
)


def _host(url: str) -> str:
    host = urlparse(url or "").netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def classify_authority(url: str, text: str = "", title: str = "", query: str = "") -> str:
    """Evidence is the host, the document type, and public-sector suffixes. The model is not asked."""
    del query
    host = _host(url)
    path = urlparse(url or "").path.lower()
    if not host:
        return "UNKNOWN"
    if any(host == item or host.endswith("." + item) for item in COMMUNITY_HOSTS):
        return "COMMUNITY"
    if any(signal in host for signal in MARKETPLACE):
        return "MARKETPLACE"
    if path.endswith((".pdf", ".csv", ".xlsx")) or path.endswith("/download"):
        if any(host.endswith(suffix) or host == suffix.lstrip(".") for suffix in PUBLIC_SECTOR_SUFFIXES):
            return "PRIMARY_DOCUMENT"
    if any(host.endswith(suffix) or host == suffix.lstrip(".") for suffix in PUBLIC_SECTOR_SUFFIXES):
        return "PRIMARY_PUBLIC_BODY"
    if any(host == item or host.endswith("." + item) for item in REPUTABLE):
        return "REPUTABLE_SECONDARY"
    from .research import rank_source

    rank = rank_source(url, text=text, query="", title=title)
    if rank == "PRIMARY_OFFICIAL":
        return "PRIMARY_OFFICIAL"
    if rank == "HIGH_QUALITY_SECONDARY":
        return "REPUTABLE_SECONDARY"
    if rank == "COMMUNITY":
        return "COMMUNITY"
    if rank == "OTHER_SECONDARY":
        return "UNKNOWN"
    return "UNKNOWN"
