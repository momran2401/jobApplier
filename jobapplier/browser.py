"""Bounded browser actions. The model never receives a generic click/execute/submit tool."""
import asyncio
import hashlib
import re
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from .security import public_url
from .store import digest

CONTROLS = 'input:not([type="hidden"]),textarea,select,[role="combobox"]'
SAFE_NEXT = re.compile(r"^(next|continue|save and continue|save & continue|review|review application)(\s*[›→>]?)$", re.I)
FINAL = re.compile(r"^(submit|submit application|submit my application|send application|apply now|finish application)$", re.I)
AUTH = re.compile(r"password|verification code|one.time|security code|captcha|social security|credit card", re.I)
READ_ONLY_HOSTS = ("linkedin.com",)
GOOGLE_BUTTON = re.compile(r"^(sign in|log in|login|continue|sign up|register)\s+with\s+google$|^google$", re.I)
SIGNIN_BUTTON = re.compile(r"^(sign in|log in|login|continue|next|submit)(\s*[›→>]?)$", re.I)
SIGNUP_BUTTON = re.compile(r"^(create account|create an account|sign up|register|continue|next)(\s*[›→>]?)$", re.I)
EMAIL_FIELD = re.compile(r"e-?mail|username|user name|login id", re.I)
PASSWORD_LABEL = re.compile(r"password", re.I)
SIGNUP_TEXT = re.compile(r"create (an |your )?account|sign up|register(ed)? (for|an)|new user", re.I)
OTP_TEXT = re.compile(r"verification code|one.time (code|password)|security code|enter the code|2-step|two.factor|captcha|verify (that )?you('re| are) human", re.I)

SNAPSHOT = r"""() => {
 const visible = e => !!(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
 const label = e => [...(e.labels || [])].map(l=>l.innerText).join(' ').trim() || e.getAttribute('aria-label') ||
   (e.getAttribute('aria-labelledby') || '').split(' ').map(id=>document.getElementById(id)?.innerText || '').join(' ').trim() ||
   e.placeholder || e.name || e.id || '';
 const fields = [...document.querySelectorAll('input:not([type="hidden"]),textarea,select,[role="combobox"]')].map((e,index)=>({
   index, label: label(e).slice(0,600), type: e.type || e.getAttribute('role') || e.tagName.toLowerCase(),
   required: e.required || e.getAttribute('aria-required')==='true', disabled: e.disabled,
   value: e.type==='password' ? '[REDACTED]' : e.type==='file' ? [...e.files].map(f=>({name:f.name,size:f.size})) :
      ['checkbox','radio'].includes(e.type) ? e.checked : (e.value ?? e.getAttribute('aria-valuetext') ?? ''),
   options: e.tagName==='SELECT' ? [...e.options].map(o=>({label:o.text,value:o.value})):[], visible:visible(e)
 })).filter(e=>e.visible || e.type==='file');
 const buttons = [...document.querySelectorAll('button,input[type="submit"],[role="button"]')].map((e,index)=>({index,
    label:(e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0,200),disabled:e.disabled,visible:visible(e)})).filter(e=>e.visible);
 const links = [...document.querySelectorAll('a[href]')].map((e,index)=>({index,label:e.innerText.trim().slice(0,150),url:e.href,visible:visible(e)}))
   .filter(e=>e.visible && /apply|application|sign in|log in/i.test(e.label)).slice(0,30);
 return {text:document.body.innerText.slice(0,20000),fields,buttons,links,
    options:[...document.querySelectorAll('[role="option"]')].filter(visible).map(e=>e.innerText.trim()).slice(0,150)};
}"""

GUARD = r"""(() => {
 window.__jobApplierCanSubmit = false;
 document.addEventListener('submit', e => {
   const b = e.submitter;
   const label = (b?.innerText || b?.value || '').trim();
   const next = /^(next|continue|save and continue|save & continue|review|review application)\s*[›→>]?$/i.test(label);
   if (!window.__jobApplierCanSubmit && !next) {e.preventDefault(); e.stopImmediatePropagation();}
 }, true);
})()"""


class Browser:
    def __init__(self, store):
        self.store = store
        self.driver = None
        self.contexts = {}
        self.pages = {}
        self.reviews = {}
        self.lock = asyncio.Lock()

    async def open(self, job):
        if job["id"] in self.pages and not self.pages[job["id"]].is_closed():
            return self.pages[job["id"]]
        url = job.get("referral_url") if job["referral_status"] == "received" and job.get("referral_url") else job["url"]
        await asyncio.to_thread(public_url, url)
        context = await self.context_for(urlsplit(job["url"]).hostname)
        page = await context.new_page()
        self.pages[job["id"]] = page
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        return page

    async def read_page(self, url, wait_ms=2500, keep_open_if=None):
        """Read-only visit in the persistent profile for that host (used for LinkedIn and company pages):
        navigate, wait, return text and links. No clicks, no typing. `keep_open_if(data)` true leaves the tab
        open (e.g. a login page the user must complete) instead of closing it."""
        await asyncio.to_thread(public_url, url)
        host = urlsplit(url).hostname or ""
        # Read-only sites are read in the background; a login page is shown by relaunching visibly.
        context = await self.context_for(host, headless=any(host.endswith(h) for h in READ_ONLY_HOSTS))
        page = await context.new_page()
        keep = False
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(wait_ms)
            data = await page.evaluate("""() => ({url: location.href, title: document.title, text: document.body.innerText.slice(0, 30000),
                links: [...document.querySelectorAll('a[href]')].map(a => ({label: a.innerText.trim().slice(0, 200), url: a.href})).filter(l => l.label).slice(0, 400)})""")
            keep = bool(keep_open_if and keep_open_if(data))
            if keep and getattr(context, "_jobapplier_headless", False):
                await page.close()
                await self.open_site(url)  # show the login page in a window the user can use
            return data
        finally:
            if not keep and not page.is_closed():
                await page.close()

    async def open_site(self, url):
        """Open a site in the app's Chrome and leave it for the user (e.g. to log in to LinkedIn once)."""
        await asyncio.to_thread(public_url, url)
        context = await self.context_for(urlsplit(url).hostname, headless=False)
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.bring_to_front()
        return page

    async def context_for(self, host, headless=False):
        """One persistent Chrome profile per site. Job portals get the submit guard; sites the user only logs
        in to and that the app only reads (LinkedIn) do not, so their login forms work normally.
        `headless` runs the same profile without a window (reads after a one-time visible login)."""
        if not self.driver:
            self.driver = await async_playwright().start()
        key = hashlib.sha256(host.encode()).hexdigest()[:20]
        existing = self.contexts.get(key)
        if existing is not None and getattr(existing, "_jobapplier_headless", False) != headless:
            await existing.close()  # same profile folder, relaunched visible/hidden as needed
            self.contexts.pop(key, None)
        if key not in self.contexts:
            context = await self.driver.chromium.launch_persistent_context(
                str(self.store.root / "browser" / key), channel=self.store.settings()["browser_channel"],
                headless=headless, viewport={"width": 1300, "height": 900}, accept_downloads=False, chromium_sandbox=True,
                # Chrome's "controlled by automated software" mode makes sites like LinkedIn bounce logins.
                ignore_default_args=["--enable-automation"], args=["--disable-blink-features=AutomationControlled"])
            if not any(host.endswith(h) for h in READ_ONLY_HOSTS):
                await context.add_init_script(GUARD)
            async def network_guard(route):
                try:
                    if route.request.url.startswith(("https://", "http://")):
                        await asyncio.to_thread(public_url, route.request.url)
                    await route.continue_()
                except (ValueError, OSError):
                    await route.abort()
            await context.route("**/*", network_guard)
            context._jobapplier_headless = headless
            self.contexts[key] = context
        return self.contexts[key]

    async def snapshot(self, page):
        frames = []
        for i, frame in enumerate(page.frames):
            try:
                data = await frame.evaluate(SNAPSHOT)
                data["frame"] = i
                frames.append(data)
            except Exception:
                continue
        return {"url": page.url, "frames": frames}

    @staticmethod
    def fingerprint(snapshot):
        return digest({"url": snapshot["url"], "frames": [{"frame": f["frame"],
                       "fields": [{k: field[k] for k in ("index", "label", "type", "value", "required", "options")}
                                  for field in f["fields"] if field["type"] not in {"submit", "button", "reset"}],
                       "buttons": [{"index": b["index"], "label": b["label"]} for b in f["buttons"] if FINAL.fullmatch(b["label"])]}
                       for f in snapshot["frames"]]})

    @staticmethod
    def detect_auth(snapshot):
        """Code-level read of a page: is it a sign-in or sign-up form, is there a Google button, is it an OTP/CAPTCHA step?
        Returns None when the page is an ordinary application form."""
        for f in snapshot["frames"]:
            fields, buttons, text = f.get("fields", []), f.get("buttons", []), f.get("text", "")
            password = next((x for x in fields if x["type"] == "password" and not x.get("disabled")), None)
            email = next((x for x in fields if x["type"] in {"email", "text"} and not x.get("disabled") and EMAIL_FIELD.search(x["label"])), None)
            google = next((b for b in buttons if not b.get("disabled") and GOOGLE_BUTTON.fullmatch(b["label"].strip())), None)
            otp = bool(OTP_TEXT.search(text[:4000])) or any(AUTH.search(x["label"]) and x["type"] != "password" for x in fields)
            if password or google or otp:
                signup = bool(SIGNUP_TEXT.search(text[:3000])) and any(x["type"] != "password" and re.search(r"first name|last name|full name|confirm", x["label"], re.I) for x in fields)
                return {"frame": f["frame"], "login": bool(password) and not signup, "signup": bool(password) and signup,
                        "otp": otp and not password, "google": google["index"] if google else None,
                        "email": email["index"] if email else None, "password": password["index"] if password else None,
                        "passwords": [x["index"] for x in fields if x["type"] == "password" and not x.get("disabled")]}
        return None

    async def click_button(self, page, frame_id, index):
        await page.frames[frame_id].locator('button,input[type="submit"],[role="button"]').nth(index).click(timeout=10000)
        await page.wait_for_timeout(1500)

    async def signin(self, page, snapshot, auth, credential):
        """Type the saved login (from the Keychain, never from the model) into the sign-in form and submit it."""
        frame = page.frames[auth["frame"]]
        fs = next(f for f in snapshot["frames"] if f["frame"] == auth["frame"])
        if auth.get("email") is not None:
            await frame.locator(CONTROLS).nth(auth["email"]).fill(credential["email"], timeout=5000)
        await frame.locator(CONTROLS).nth(auth["password"]).fill(credential["password"], timeout=5000)
        button = next((b for b in fs["buttons"] if not b["disabled"] and SIGNIN_BUTTON.fullmatch(b["label"].strip())), None)
        if button:
            await self.click_button(page, auth["frame"], button["index"])
        else:
            await frame.locator(CONTROLS).nth(auth["password"]).press("Enter")
            await page.wait_for_timeout(1500)

    async def signup(self, page, snapshot, auth, credential, identity):
        """Create the account with the saved login and the profile's name; verification codes are handed off after."""
        frame = page.frames[auth["frame"]]
        fs = next(f for f in snapshot["frames"] if f["frame"] == auth["frame"])
        for field in fs["fields"]:
            if field["disabled"] or field["type"] in {"checkbox", "radio", "file", "submit", "button", "select", "combobox"}:
                continue
            label = field["label"]
            value = None
            if field["type"] == "password":
                value = credential["password"]
            elif re.search(r"first name", label, re.I):
                value = identity.get("first_name")
            elif re.search(r"last name|surname", label, re.I):
                value = identity.get("last_name")
            elif re.search(r"full name|^name$", label, re.I):
                value = " ".join(x for x in (identity.get("first_name"), identity.get("last_name")) if x)
            elif EMAIL_FIELD.search(label):
                value = credential["email"]
            elif re.search(r"phone", label, re.I):
                value = identity.get("phone")
            if value:
                await frame.locator(CONTROLS).nth(field["index"]).fill(str(value), timeout=5000)
        button = next((b for b in fs["buttons"] if not b["disabled"] and SIGNUP_BUTTON.fullmatch(b["label"].strip())), None)
        if button:
            await self.click_button(page, auth["frame"], button["index"])

    async def google_chooser(self, page, email):
        """On Google's account chooser, pick the saved account (a deterministic click on the user's own email)."""
        if (urlsplit(page.url).hostname or "") != "accounts.google.com" or not email:
            return False
        entry = page.locator(f'[data-email="{email}"], [data-identifier="{email}"]').first
        if await entry.count() == 0:
            entry = page.get_by_text(email, exact=True).first
        if await entry.count() == 0:
            return False
        await entry.click(timeout=5000)
        await page.wait_for_timeout(2500)
        return True

    async def act(self, page, snapshot, action, answers, attachments):
        kind = action.get("action")
        frame_id = int(action.get("frame", 0))
        fs = next((f for f in snapshot["frames"] if f["frame"] == frame_id), None)
        if not fs or frame_id >= len(page.frames):
            raise ValueError("The form changed. Resume preparation.")
        frame = page.frames[frame_id]
        index = int(action.get("index", -1))
        if kind in {"fill", "select", "check", "upload", "open_options"}:
            field = next((f for f in fs["fields"] if f["index"] == index), None)
            if not field or field["disabled"] or AUTH.search(field["label"]) or field["type"] == "password":
                raise ValueError("This field requires your direct input in the browser.")
            locator = frame.locator(CONTROLS).nth(index)
            if kind == "open_options":
                if field["type"] != "combobox":
                    raise ValueError("Only a dropdown can be opened through this action.")
                await locator.click(timeout=5000)
                return None
            if kind == "upload":
                key = action.get("document")
                if field["type"] != "file" or key not in attachments:
                    raise ValueError("The required document is not prepared. Add it before continuing.")
                await locator.set_input_files(attachments[key], timeout=10000)
                return {"question": field["label"], "answer": key + " attached", "source": key}
            key = action.get("answer_key")
            if key not in answers:
                raise ValueError("The agent requested an answer that is not in your verified answer bank.")
            value = answers[key]
            if kind == "fill":
                if field["type"] in {"checkbox", "radio", "file", "submit", "button", "reset"}:
                    raise ValueError("Unsupported fill action.")
                await locator.fill(str(value), timeout=5000)
            elif kind == "select":
                option = next((o for o in field["options"] if str(value).lower() in {o["label"].lower(), o["value"].lower()}), None)
                if not option:
                    raise ValueError(f"Choose an exact option for: {field['label']}")
                await locator.select_option(value=option["value"], timeout=5000)
            elif kind == "check":
                if field["type"] not in {"checkbox", "radio"} or str(value).lower() not in {"true", "false", "yes", "no"}:
                    raise ValueError("An explicit yes/no answer is needed for this checkbox.")
                await locator.set_checked(str(value).lower() in {"true", "yes"}, timeout=5000)
            return {"question": field["label"], "answer": value, "source": key}
        if kind == "choose_option":
            key = action.get("answer_key")
            if key not in answers or str(answers[key]) not in fs["options"]:
                raise ValueError("Choose an exact dropdown answer in your profile.")
            await frame.get_by_role("option", name=str(answers[key]), exact=True).click(timeout=5000)
            return None
        if kind == "next":
            button = next((b for b in fs["buttons"] if b["index"] == index), None)
            if not button or button["disabled"] or not SAFE_NEXT.fullmatch(button["label"]):
                raise ValueError("This button is not an allowed preparation step.")
            await frame.locator('button,input[type="submit"],[role="button"]').nth(index).click(timeout=10000)
            return None
        if kind == "open_link":
            link = next((item for item in fs["links"] if item["index"] == index), None)
            if not link:
                raise ValueError("The requested application link is not visible.")
            await asyncio.to_thread(public_url, link["url"])
            await page.goto(link["url"], wait_until="domcontentloaded", timeout=45000)
            return None
        raise ValueError("Unsupported browser action. No action was taken.")

    async def capture_review(self, jid, page):
        snapshot = await self.snapshot(page)
        buttons = [(f["frame"], b) for f in snapshot["frames"] for b in f["buttons"]
                   if FINAL.fullmatch(b["label"]) and not b["disabled"]]
        if len(buttons) != 1:
            raise ValueError("Cannot identify one unambiguous final submit button. Complete the remaining steps in the browser, then resume.")
        missing = [f["label"] for frame in snapshot["frames"] for f in frame["fields"]
                   if f["required"] and not f["disabled"] and not f["value"] and f["type"] != "radio"]
        if missing:
            raise ValueError("Required fields remain: " + "; ".join(missing)[:500])
        target = {"frame": buttons[0][0], "index": buttons[0][1]["index"], "label": buttons[0][1]["label"]}
        fingerprint = self.fingerprint(snapshot)
        self.reviews[jid] = {"fingerprint": fingerprint, "target": target}
        path = self.store.root / "artifacts" / jid / "review.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=True)
        return {"snapshot": snapshot, "fingerprint": fingerprint, "target": target,
                "screenshot": str(path.relative_to(self.store.root))}

    async def verify(self, job):
        page = self.pages.get(job["id"])
        if not page or page.is_closed() or job["id"] not in self.reviews:
            raise ValueError("The reviewed browser tab is no longer available. Resume preparation.")
        snapshot = await self.snapshot(page)
        if self.fingerprint(snapshot) != job["package"].get("fingerprint"):
            raise ValueError("The form changed since review. Prepare and approve the updated application.")
        for artifact in [job["package"].get("resume"), *job["package"].get("attachments", [])]:
            if artifact:
                path = (self.store.root / artifact["path"]).resolve()
                if not path.is_relative_to(self.store.root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
                    raise ValueError("An approved attachment changed or is missing. Prepare again.")
        return page

    async def submit(self, job):
        page = await self.verify(job)
        target = job["package"]["target"]
        frame = page.frames[target["frame"]]
        await frame.evaluate("window.__jobApplierCanSubmit = true")
        try:
            await frame.locator('button,input[type="submit"],[role="button"]').nth(target["index"]).click(timeout=20000)
            # Wait for a confirmation signal, not just navigation or disappearance of the form.
            try:
                await page.get_by_text(re.compile(r"application (has been |was )?(successfully )?(submitted|received)|thank you for applying|thanks for applying", re.I)).first.wait_for(timeout=15000)
            except Exception:
                pass
            text = await page.locator("body").inner_text()
            success = re.search(r"application (?:has been |was )?(?:successfully )?(?:submitted|received)|thank you for applying|thanks for applying", text, re.I)
            path = self.store.root / "artifacts" / job["id"] / "confirmation.png"
            await page.screenshot(path=str(path), full_page=True)
            return {"confirmed": bool(success), "confirmation": success.group(0) if success else "No explicit confirmation detected; check the portal.",
                    "status_url": page.url, "receipt": str(path.relative_to(self.store.root))}
        finally:
            try:
                await frame.evaluate("window.__jobApplierCanSubmit = false")
            except Exception:
                pass

    async def close(self):
        for context in self.contexts.values():
            await context.close()
        if self.driver:
            await self.driver.stop()
