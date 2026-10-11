"""Workspace sync between your own Macs through a folder you already keep in sync (iCloud Drive by default).

The live database never goes in that folder. Each machine writes one encrypted snapshot of its records
(<machine-id>.json.enc) plus encrypted copies of the files those records point to (files/<sha256>.bin), and reads
the other machines' snapshots. Merging is per record, newest `updated_at` wins; deletions travel as tombstones.
Encryption is AES-GCM with a key derived (scrypt) from a passphrase you enter once per Mac; it lives in that
Mac's Keychain, never in the folder. Keychain secrets (API key, tracker token, company passwords) don't sync."""
import asyncio
import hashlib
import json
import os
import platform
import secrets
import shutil
import uuid
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .store import normalize_company, now

MAGIC = b"JAS1"
FORMAT = 1
DEFAULT_FOLDER = "~/Library/Mobile Documents/com~apple~CloudDocs/JobApplier Sync"
# kv keys that describe this machine only, or that the sync itself maintains.
LOCAL_KV = {"last_inference", "usage", "sync_state", "tombstones", "kv_times", "profile_meta", "company_map"}  # profile_meta travels with the profile text
FILE_DIRS = ("documents", "artifacts", "logos")
LOCAL_EVENTS = ("Synced ", "Backed up the workspace")  # about this Mac's sync, not about your search
MAX_FILE = 25 * 1024 * 1024


# ---- crypto ----
def derive_key(passphrase, salt):
    return hashlib.scrypt(passphrase.encode(), salt=salt, n=2 ** 14, r=8, p=1, maxmem=128 * 1024 * 1024, dklen=32)


def encrypt(key, data):
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, MAGIC)


def decrypt(key, blob):
    if blob[:4] != MAGIC:
        raise ValueError("Not a JobApplier sync file.")
    try:
        return AESGCM(key).decrypt(blob[4:16], blob[16:], MAGIC)
    except InvalidTag:
        raise ValueError("The sync passphrase does not match the one used on the other Mac.") from None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_atomic(path, data):
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


class Sync:
    def __init__(self, store, passphrase_getter):
        self.store = store
        self.passphrase = passphrase_getter  # () -> str | None, read from the Keychain each time
        self.task = None
        self.lock = asyncio.Lock()
        self.error = ""

    # ---- identity & config ----
    def machine(self):
        path = self.store.root / "machine.json"
        if path.is_file():
            return json.loads(path.read_text())
        info = {"id": uuid.uuid4().hex[:12], "name": platform.node().removesuffix(".local") or "Mac"}
        path.write_text(json.dumps(info))
        return info

    def folder(self):
        settings = self.store.settings()
        return Path(os.path.expanduser(settings.get("sync_folder") or DEFAULT_FOLDER))

    def key(self):
        passphrase = self.passphrase()
        if not passphrase:
            raise ValueError("Set the sync passphrase in Settings first (the same one on every Mac).")
        folder = self.folder()
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "files").mkdir(exist_ok=True)
        salt_path = folder / "salt"
        if not salt_path.is_file():
            write_atomic(salt_path, secrets.token_bytes(16))
        return derive_key(passphrase, salt_path.read_bytes())

    def state(self):
        s = self.store.get("sync_state", {})
        settings = self.store.settings()
        folder = self.folder()
        peers = []
        if folder.is_dir():
            me = self.machine()["id"]
            for p in folder.glob("*.json.enc"):
                mid = p.name[: -len(".json.enc")]
                if mid != me:
                    peers.append({"id": mid, "name": s.get("peers", {}).get(mid, {}).get("name", mid), "written_at": s.get("peers", {}).get(mid, {}).get("written_at"),
                                  "file_modified": now_from(p.stat().st_mtime)})
        return {"enabled": bool(settings.get("sync_enabled")), "folder": str(folder), "folder_exists": folder.is_dir(),
                "has_passphrase": bool(self.passphrase()), "machine": self.machine(), "last_push": s.get("last_push"),
                "last_pull": s.get("last_pull"), "peers": peers, "error": self.error, "minutes": settings.get("sync_minutes", 1)}

    # ---- snapshot ----
    def local_files(self):
        """Files worth carrying: uploads, built artifacts (PDFs, screenshots), logos. Path -> sha256."""
        out = {}
        for d in FILE_DIRS:
            base = self.store.root / d
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if path.is_file() and path.stat().st_size <= MAX_FILE and not path.name.startswith("."):
                    out[str(path.relative_to(self.store.root))] = {"sha256": sha256(path), "size": path.stat().st_size}
        return out

    def snapshot(self):
        kv_times = self.store.kv_times()
        kv = {}
        for key, value in self.store.kv_items():
            if key in LOCAL_KV:
                continue
            kv[key] = {"value": value, "updated_at": kv_times.get(key, "")}
        meta = self.store.get("profile_meta", {})
        return {"format": FORMAT, "machine": self.machine(), "written_at": now(),
                "documents": self.store.documents(),
                "jobs": self.store.jobs(), "companies": self.store.companies(), "people": self.store.people(), "kv": kv,
                "events": self.store.events_all(limit=500), "tombstones": self.store.get("tombstones", {}),
                "profile": {"text": self.store.profile_text(), "updated_at": meta.get("updated_at") or "", "verified": bool(meta.get("verified"))},
                "files": self.local_files()}

    def push(self):
        """Write this machine's snapshot and any file blobs the folder doesn't have yet. Skipped when nothing changed."""
        key = self.key()
        snap = self.snapshot()
        digest = hashlib.sha256(json.dumps({k: v for k, v in snap.items() if k != "written_at"}, sort_keys=True).encode()).hexdigest()
        state = self.store.get("sync_state", {})
        if state.get("last_digest") == digest:
            return False
        folder = self.folder()
        for rel, info in snap["files"].items():
            blob = folder / "files" / (info["sha256"] + ".bin")
            if not blob.is_file():
                write_atomic(blob, encrypt(key, (self.store.root / rel).read_bytes()))
        write_atomic(folder / f"{snap['machine']['id']}.json.enc", encrypt(key, json.dumps(snap).encode()))
        self.store.set("sync_state", {**state, "last_digest": digest, "last_push": snap["written_at"]})
        return True

    def pull(self):
        """Read every other machine's snapshot that changed since last time and merge it. Returns machines applied."""
        key = self.key()
        folder = self.folder()
        me = self.machine()["id"]
        state = self.store.get("sync_state", {})
        peers = dict(state.get("peers", {}))
        applied = []
        for path in sorted(folder.glob("*.json.enc")):
            mid = path.name[: -len(".json.enc")]
            if mid == me:
                continue
            if peers.get(mid, {}).get("file_mtime") == path.stat().st_mtime:
                continue
            snap = json.loads(decrypt(key, path.read_bytes()))
            if snap.get("format") != FORMAT:
                raise ValueError(f"Snapshot from {mid} uses sync format {snap.get('format')}; update JobApplier on both Macs.")
            if mid not in peers:
                self.backup(f"before-first-sync-{mid}")
            self.apply(snap, key)
            peers[mid] = {"name": snap["machine"]["name"], "written_at": snap["written_at"], "file_mtime": path.stat().st_mtime}
            applied.append(snap["machine"]["name"])
        if applied:
            self.store.set("sync_state", {**self.store.get("sync_state", {}), "peers": peers, "last_pull": now()})
        return applied

    def backup(self, label):
        """A consistent copy of the database and profile, taken before the first merge with a new Mac."""
        import sqlite3
        out = self.store.root / "backups" / f"{now()[:19].replace(':', '')}-{label}"
        out.mkdir(parents=True, exist_ok=True)
        src, dst = sqlite3.connect(self.store.path), sqlite3.connect(out / "workspace.sqlite")
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        if self.store.profile_path.is_file():
            shutil.copy2(self.store.profile_path, out / "profile.md")
        self.store.event(None, f"Backed up the workspace to data/backups/{out.name} before the first sync with another Mac.")
        return out

    def match_companies(self, remote_companies):
        """Both Macs may have created the same employer separately, under different IDs. A remote company that
        is not known by ID is matched to a local one by name keys (the same rule jobs use to find their company)
        and remembered in kv `company_map`, so it is merged instead of duplicated. Returns {remote_id: local_id}."""
        mapping = dict(self.store.get("company_map", {}))
        local = self.store.companies()
        ids = {c["id"] for c in local}
        for company in remote_companies:
            if company["id"] in ids or company["id"] in mapping:
                continue
            keys = set(company.get("keys") or []) | {normalize_company(company.get("name"))}
            keys.discard("")
            for mine in local:
                known = set(mine.get("keys") or []) | {normalize_company(mine.get("name"))}
                if any(k == x or (len(k) >= 4 and len(x) >= 4 and (k.startswith(x) or x.startswith(k))) for k in keys for x in known if x):
                    mapping[company["id"]] = mine["id"]
                    break
        self.store.set("company_map", mapping)
        return mapping

    # ---- merge ----
    def apply(self, snap, key):
        store = self.store
        changed = 0
        # Files first, so records that point to them never dangle.
        folder = self.folder()
        for rel, info in snap.get("files", {}).items():
            target = (store.root / rel).resolve()
            if not target.is_relative_to(store.root) or rel.split("/")[0] not in FILE_DIRS:
                continue
            if target.is_file() and (target.stat().st_size == info["size"]):
                continue
            blob = folder / "files" / (info["sha256"] + ".bin")
            if blob.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                write_atomic(target, decrypt(key, blob.read_bytes()))
        local_docs = store.documents()
        have = {d["id"] for d in local_docs} | {d.get("sha256") for d in local_docs if d.get("sha256")}
        for doc in reversed(snap.get("documents", [])):  # oldest first, so the list keeps its order
            if doc["id"] in have or (doc.get("sha256") and doc["sha256"] in have):
                continue  # already here, or the same file uploaded on both Macs
            if (store.root / doc["path"]).is_file():
                store.save_document(doc)
                changed += 1
        mapping = self.match_companies(snap.get("companies", []))
        local_companies = {c["id"]: c for c in store.companies()}
        for company in snap.get("companies", []):
            if company["id"] in mapping:  # same employer under another ID: fold what is new into ours
                mine = local_companies[mapping[company["id"]]]
                merged = {**mine, "keys": sorted(set(mine.get("keys") or []) | set(company.get("keys") or [])),
                          "aliases": sorted((set(mine.get("aliases") or []) | set(company.get("aliases") or []) | {company["name"]}) - {mine["name"]})}
                for field in ("domain", "research", "logo", "account_email", "portal_url", "notes"):
                    if not mine.get(field) and company.get(field):
                        merged[field] = company[field]
                if mine.get("signin_method", "unknown") == "unknown" and company.get("signin_method", "unknown") != "unknown":
                    merged.update(signin_method=company["signin_method"], google_signin=company.get("google_signin", False), has_account=company.get("has_account", False))
                asked = {q["question"].strip().lower() for q in mine.get("questions") or []}
                merged["questions"] = (mine.get("questions") or []) + [q for q in company.get("questions") or [] if q["question"].strip().lower() not in asked]
                if merged != mine:
                    store.save_company({**merged, "updated_at": max(mine.get("updated_at", ""), company.get("updated_at", ""))}, touch=False)
                    changed += 1
                continue
            mine = local_companies.get(company["id"])
            if mine is None or company.get("updated_at", "") > mine.get("updated_at", ""):
                store.save_company({**company, "updated_at": company.get("updated_at") or now()}, touch=False)
                changed += 1
        tombstones = {**store.get("tombstones", {}), **snap.get("tombstones", {})}
        local_jobs = {j["id"]: j for j in store.jobs()}
        by_url = {j["url"]: j["id"] for j in local_jobs.values()}
        for jid, ts in tombstones.items():  # jobs deleted on another Mac after our last edit
            if jid in local_jobs and ts > local_jobs[jid].get("updated_at", ""):
                store.delete_job(jid, tombstone=False)
                local_jobs.pop(jid)
                changed += 1
        for job in snap.get("jobs", []):
            if tombstones.get(job["id"], "") > job.get("updated_at", ""):
                continue
            if job.get("company_id") in mapping:
                job = {**job, "company_id": mapping[job["company_id"]]}
            mine = local_jobs.get(job["id"])
            if mine is None and job["url"] in by_url:
                continue  # the same link was added on both Macs; the local record stays
            if mine is None or job.get("updated_at", "") > mine.get("updated_at", ""):
                job["revision"] = max(job.get("revision", 1), (mine or {}).get("revision", 0)) + 1
                store.put_job(job)
                changed += 1
        local_people = {p["id"]: p for p in store.people()}
        by_link = {p["profile_url"].lower(): p for p in local_people.values() if p.get("profile_url")}
        for person in snap.get("people", []):
            if tombstones.get(person["id"], "") > person.get("updated_at", ""):
                continue
            if person.get("company_id") in mapping:
                person = {**person, "company_id": mapping[person["company_id"]]}
            mine = local_people.get(person["id"])
            if mine and (mine.get("profile_url") or "").lower() != (person.get("profile_url") or "").lower() and (mine.get("profile_url") or person.get("profile_url")):
                # Both Macs handed out the same ID to different people while apart: the local one moves to a fresh ID.
                moved = {**mine, "id": store.new_person_id(), "updated_at": now()}
                store.delete_person(mine["id"], tombstone=False)
                store.save_person(moved)
                mine = None
            twin = by_link.get((person.get("profile_url") or "").lower())
            if mine is None and twin and twin["id"] != person["id"]:
                continue  # same profile added on both Macs under different IDs; keep ours
            if mine is None or person.get("updated_at", "") > mine.get("updated_at", ""):
                store.save_person(person)
                changed += 1
        for pid, ts in tombstones.items():
            mine = local_people.get(pid)
            if mine and ts > mine.get("updated_at", ""):
                store.delete_person(pid, tombstone=False)
                changed += 1
        store.set("tombstones", tombstones)
        kv_times = store.kv_times()
        for k, item in snap.get("kv", {}).items():
            if k in LOCAL_KV:
                continue
            if k == "person_seq":
                store.set(k, max(int(item["value"] or 0), int(store.get(k, 0) or 0)))
                continue
            if item.get("updated_at", "") > kv_times.get(k, ""):
                store.set(k, item["value"], at=item.get("updated_at"))
                changed += 1
        profile = snap.get("profile") or {}
        meta = store.get("profile_meta", {})
        if profile.get("text") and profile.get("updated_at", "") > (meta.get("updated_at") or ""):
            if profile["text"] != store.profile_text():
                store.save_profile_text(profile["text"], reason="sync")
                changed += 1
            store.set("profile_meta", {**store.get("profile_meta", {}), "updated_at": profile["updated_at"], "verified": bool(profile.get("verified"))})
        for e in snap.get("events", []):
            if not e["message"].startswith(LOCAL_EVENTS):
                store.add_event_if_missing(e["at"], e.get("job_id"), e["message"])
        if changed:
            store.event(None, f"Synced {changed} change(s) from {snap['machine']['name']}.")
        return changed

    # ---- loop ----
    async def run_once(self):
        async with self.lock:
            try:
                applied = await asyncio.to_thread(self.pull)
                pushed = await asyncio.to_thread(self.push)
                self.error = ""
                return {"applied": applied, "pushed": pushed}
            except Exception as exc:
                self.error = str(exc)[:300]
                raise

    def start_background(self):
        self.stop()
        settings = self.store.settings()
        if not settings.get("sync_enabled"):
            return

        async def loop():
            while True:
                try:
                    await self.run_once()
                except Exception:
                    pass
                await asyncio.sleep(max(1, int(settings.get("sync_minutes", 1))) * 60)
        self.task = asyncio.create_task(loop())

    def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = None

    async def flush(self):
        """On shutdown: write the final state so the other Mac picks it up."""
        if self.store.settings().get("sync_enabled") and self.passphrase():
            try:
                await asyncio.to_thread(self.push)
            except Exception:
                pass


def now_from(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def remove_folder_contents(folder):
    """For tests and for 'start over': delete everything the sync wrote."""
    shutil.rmtree(folder, ignore_errors=True)
