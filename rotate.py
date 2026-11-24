#!/usr/bin/env python3
"""Sign in to agentrouter.org once per GitHub account, then sign out.

Drives a separate headless Opera profile over a localhost debug port, so it also
runs while the desktop is locked. Never solves a slider/captcha and never approves
a new OAuth grant: it reports what is needed, waits, then gives up on that account.
A human can only step in with --headed, which opens a window.

  rotate.py                  all accounts in accounts.txt
  rotate.py --only NAME      a single account
  rotate.py --headed         show the Opera window, for a step that needs a human
  rotate.py --keep-open      leave Opera running afterwards
"""
import argparse, json, os, pathlib, subprocess, sys, time, urllib.request
from playwright.sync_api import sync_playwright

W = pathlib.Path.home() / ".local/share/agentrouter-rotate"
PROFILE, DEBUG = W / "opera-profile", W / "debug"
PORT, SITE = 9333, "https://agentrouter.org"
HUMAN_WAIT = 90


class Stop(Exception):
    pass


class RateLimited(Stop):
    """The site answered 429: end the whole run instead of pushing on."""


def cdp_up():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=1)
        return True
    except OSError:
        return False


def ensure_opera(headed=False):
    if cdp_up():
        return None
    args = ["/usr/bin/opera", f"--user-data-dir={PROFILE}", f"--remote-debugging-port={PORT}",
            "--no-first-run", "about:blank"]
    env = dict(os.environ)
    if not headed:
        # No window and no dependency on the display session, so a locked or
        # disconnected desktop cannot stop the run. D-Bus stays: cookies need the keyring.
        args.insert(1, "--headless=new")
        for var in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY"):
            env.pop(var, None)
    with open(W / "opera.log", "ab") as log:
        proc = subprocess.Popen(args, stdout=log, stderr=log, env=env, start_new_session=True)
    for _ in range(30):
        if cdp_up():
            return proc
        time.sleep(1)
    raise SystemExit("Opera debug port did not come up")


FIND_SWITCH_FORM = """name => {
  for (const f of document.querySelectorAll("form[action$='/switch_account']")) {
    let e = f.parentElement;
    while (e && !(e.innerText || '').trim()) e = e.parentElement;
    if (e && e.querySelectorAll("form[action$='/switch_account']").length === 1
          && e.innerText.split(/\\s+/).includes(name)) {
      window.__switchForm = f;
      return true;
    }
  }
  return false;
}"""


def _no_row():
    raise Stop("account is not in the GitHub switcher (its session expired?)")


def switch_github(page, name):
    page.goto("https://github.com/switch_account", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    if "/login" in page.url:
        raise Stop("GitHub session expired for the whole profile")
    current = page.locator('meta[name="user-login"]').get_attribute("content")
    if current != name:
        # The username sits outside the empty <form>; walk up to the row that holds it.
        with page.expect_navigation(timeout=30000) if page.evaluate(FIND_SWITCH_FORM, name) else _no_row():
            page.evaluate("name => { window.__switchForm.requestSubmit(); }", name)
        page.wait_for_timeout(2000)
    page.goto("https://github.com/", wait_until="domcontentloaded", timeout=60000)
    current = page.locator('meta[name="user-login"]').get_attribute("content")
    if current != name:
        raise Stop(f"GitHub switch failed, still {current}")


def site_user(page):
    raw = page.evaluate("localStorage.getItem('user')")
    return json.loads(raw) if raw else None


def login(ctx, page):
    page.goto(f"{SITE}/login", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)
    statuses = []
    page.on("response", lambda r: statuses.append(r.status) if "/api/oauth/state" in r.url else None)
    page.get_by_text("Continue with GitHub").first.click()
    deadline, told, picked = time.time() + HUMAN_WAIT, False, set()
    while time.time() < deadline:
        if 429 in statuses:
            raise RateLimited("site rate limit (HTTP 429)")
        for p in ctx.pages:
            url = p.url
            if url.startswith(SITE) and "/login" not in url:
                try:
                    user = site_user(p)
                except Exception:
                    continue
                if user:
                    return p, user
            needs = None
            if "github.com/login/oauth/select_account" in url:
                # switch_github() already verified the current account; this form picks it.
                form = p.locator("form[action*='authorize_app']")
                if form.count() and p not in picked:
                    picked.add(p)
                    form.first.evaluate("f => f.requestSubmit()")
                continue
            if "github.com/login/oauth" in url and p.locator("button[name=authorize]").count():
                needs = "approve the GitHub OAuth grant"
            elif "github.com/sessions/two-factor" in url or url.rstrip("/").endswith("github.com/login"):
                needs = "sign in to GitHub"
            elif url.startswith(SITE) and p.get_by_text("Access Verification").count():
                needs = "complete the site's verification slider"
            if needs and not told:
                print(f"  ACTION NEEDED: {needs} ({HUMAN_WAIT}s; only possible in a --headed run)", flush=True)
                told = True
        page.wait_for_timeout(1000)
    raise Stop("sign-in did not finish in time")


def logout(ctx, page):
    page.evaluate("fetch('/api/user/logout', {credentials: 'include'}).then(r => r.status)")
    page.evaluate("localStorage.clear()")
    ctx.clear_cookies(domain="agentrouter.org")
    page.goto(f"{SITE}/console", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    if site_user(page):
        raise Stop("still signed in after logout")


def run_one(ctx, name):
    page = ctx.new_page()
    try:
        switch_github(page, name)
        site, user = login(ctx, page)
        page.wait_for_timeout(4000)
        user = site_user(site) or user
        self_info = site.evaluate(
            """id => fetch('/api/user/self', {credentials: 'include', headers: {'New-Api-User': String(id)}})
                 .then(r => r.json())""", user.get("id"))
        data = self_info.get("data") or {}
        logout(ctx, site)
        return {"id": user.get("id"), "username": user.get("username"),
                "quota_usd": round(data["quota"] / 500000, 2) if "quota" in data else None,
                "api_message": self_info.get("message") or None}
    finally:
        DEBUG.mkdir(exist_ok=True)
        for i, p in enumerate(list(ctx.pages)):
            if p.url != "about:blank":
                print(f"  last url[{i}]: {p.url.split('?')[0]}", flush=True)
                try:
                    p.close()
                except Exception:
                    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument("--pause", type=int, default=60, help="seconds between accounts")
    a = ap.parse_args()
    names = [a.only] if a.only else [
        n.strip() for n in (W / "accounts.txt").read_text().splitlines() if n.strip()]
    proc, failed, seen = ensure_opera(a.headed), 0, {}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
        ctx = browser.contexts[0]
        keep = ctx.pages[0] if ctx.pages else ctx.new_page()  # stops Opera closing with the last tab
        for extra in ctx.pages[1:]:
            extra.close()
        ctx.clear_cookies(domain="agentrouter.org")
        for i, name in enumerate(names):
            if i:
                time.sleep(a.pause)
            print(f"[{name}]", flush=True)
            try:
                try:
                    r = run_one(ctx, name)
                except Exception as e:
                    if "ERR_NETWORK" not in str(e):
                        raise
                    print("  network blip, retrying once", flush=True)
                    ctx.clear_cookies(domain="agentrouter.org")
                    time.sleep(10)
                    r = run_one(ctx, name)
                if r["id"] in seen:
                    raise Stop(f"same site user as {seen[r['id']]}: wrong account used")
                seen[r["id"]] = name
                print(f"  OK {json.dumps(r)}", flush=True)
            except RateLimited as e:
                failed += len(names) - i
                print(f"  STOPPED {e}: skipping {len(names) - i} account(s), try again later", flush=True)
                ctx.clear_cookies(domain="agentrouter.org")
                break
            except Exception as e:
                failed += 1
                print(f"  FAILED {str(e).splitlines()[0][:200]}", flush=True)
                ctx.clear_cookies(domain="agentrouter.org")
        if not a.keep_open:
            browser.new_browser_cdp_session().send("Browser.close")
    print(f"done: {len(names) - failed} ok, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
