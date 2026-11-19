"""Shared paths and source policy. config/domains.json is the single source of truth."""

import json
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"


@cache
def domains() -> dict:
    return json.loads((CONFIG_DIR / "domains.json").read_text())


def allowed_domains() -> list[str]:
    return list(domains()["allow"])


def is_allowed_url(url: str) -> bool:
    """True only if the URL's host is an allowlisted domain (or subdomain) and not blocked."""
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False

    def matches(d: str) -> bool:
        return host == d or host.endswith("." + d)

    if any(matches(d) for d in domains()["block"]):
        return False
    return any(matches(d) for d in domains()["allow"])
