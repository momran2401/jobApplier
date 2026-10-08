"""Company logos: fetched once from the company's website (DuckDuckGo's icon service, then the site's own
apple-touch-icon), cached under data/logos, served by the app so the page never loads third-party images."""
import asyncio
import re
from urllib.parse import urljoin, urlsplit

import httpx

from .security import public_url
from .store import now

MAGIC = {b"\x89PNG": "png", b"\xff\xd8\xff": "jpg", b"\x00\x00\x01\x00": "ico", b"GIF8": "gif", b"RIFF": "webp"}
MAX_BYTES = 2 * 1024 * 1024
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}


def root_domain(host):
    """jobs.nvidia.com -> nvidia.com; careers.foo.co.uk -> foo.co.uk."""
    parts = (host or "").lower().strip().split(".")
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return ""
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def company_domain(company):
    website = (company.get("research") or {}).get("website") or ""
    host = urlsplit(website if "://" in website else "https://" + website).hostname if website else ""
    return root_domain(host or company.get("domain") or "")


def kind_of(data):
    if data.lstrip()[:5].lower().startswith((b"<svg", b"<?xml")):
        return "svg"
    return next((ext for magic, ext in MAGIC.items() if data.startswith(magic)), None)


async def fetch_logo(domain):
    """Returns (bytes, ext) or None. Every URL is checked against the private-network guard first."""
    if not domain:
        return None
    async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 JobApplier/0.1"}) as client:
        for url in (f"https://icons.duckduckgo.com/ip3/{domain}.ico", f"https://{domain}/apple-touch-icon.png", f"https://{domain}/favicon.ico"):
            data = await _get_image(client, url)
            if data:
                return data
        try:  # the site's declared icons, largest first
            await asyncio.to_thread(public_url, f"https://{domain}/")
            page = await client.get(f"https://{domain}/")
            links = re.findall(r"<link[^>]+rel=[\"']([^\"']*icon[^\"']*)[\"'][^>]*href=[\"']([^\"']+)[\"']", page.text[:200000], re.I)
            links += [(rel, href) for href, rel in re.findall(r"<link[^>]+href=[\"']([^\"']+)[\"'][^>]*rel=[\"']([^\"']*icon[^\"']*)[\"']", page.text[:200000], re.I)]
            links.sort(key=lambda x: ("apple" not in x[0].lower(), x[0]))
            for _, href in links[:4]:
                data = await _get_image(client, urljoin(str(page.url), href))
                if data:
                    return data
        except Exception:
            return None
    return None


async def _get_image(client, url):
    try:
        await asyncio.to_thread(public_url, url)
        r = await client.get(url)
    except Exception:
        return None
    if r.status_code != 200 or len(r.content) > MAX_BYTES or len(r.content) < 100:
        return None
    ext = kind_of(r.content)
    return (r.content, ext) if ext else None


async def ensure_logo(store, company, force=False):
    """Fetch and cache the logo for one company; records the attempt so unknown domains aren't retried constantly."""
    if company.get("logo") and not force:
        return company
    if company.get("logo_checked_at") and not force:
        return company
    domain = company_domain(company)
    found = await fetch_logo(domain) if domain else None
    folder = store.root / "logos"
    folder.mkdir(exist_ok=True, mode=0o700)
    company = store.company(company["id"])
    if found:
        data, ext = found
        path = folder / f"{company['id']}.{ext}"
        path.write_bytes(data)
        company["logo"] = path.name
    company["logo_checked_at"] = now()
    return store.save_company(company)


async def fill_missing(store):
    for company in store.companies():
        try:
            await ensure_logo(store, company)
        except Exception:
            continue
