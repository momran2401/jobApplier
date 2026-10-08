import ipaddress
import json
import os
import socket
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import keyring

SERVICE = "JobApplier"


def save_secret(name, value):
    if value:
        keyring.set_password(SERVICE, name, value)
    else:
        try:
            keyring.delete_password(SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            pass
    secret.cache_clear()


@lru_cache(maxsize=8)
def secret(name):
    env = {"anthropic_key": "ANTHROPIC_API_KEY"}.get(name)
    if env and os.environ.get(env):
        return os.environ[env]
    if name == "anthropic_key":
        path = Path(__file__).resolve().parent.parent / ".env"
        if path.is_file():
            for line in path.read_text().splitlines():
                if line.strip().startswith("ANTHROPIC_API_KEY="):
                    value = line.strip().split("=", 1)[1].strip().strip("\"'")
                    if value:
                        return value
    try:
        return keyring.get_password(SERVICE, name)
    except keyring.errors.KeyringError:
        return None


def company_credential(cid):
    """The saved {email, password} for a company from the Keychain, or None."""
    raw = secret("company:" + cid)
    try:
        value = json.loads(raw) if raw else None
    except ValueError:
        return None
    return value if isinstance(value, dict) and value.get("password") else None


def public_url(url):
    p = urlsplit(url)
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("Only public HTTP(S) URLs are supported.")
    if p.port and p.port not in {80, 443}:
        raise ValueError("Nonstandard ports are not supported for job sites.")
    try:
        addresses = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        raise ValueError("The website hostname could not be resolved.") from None
    if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("Local and private network URLs are not allowed as job or portfolio sources.")
    return url
