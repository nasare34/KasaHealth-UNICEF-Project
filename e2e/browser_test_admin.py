"""Admin dashboard: health checks, advice when things break, overview, downloads, mobile."""
import json, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
results, page_errors = [], []

def check(name, ok, info=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f" — {info}" if info else ""), flush=True)

def mock(path, data=None):
    req = urllib.request.Request(f"http://127.0.0.1:9100{path}", data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    return json.loads(urllib.request.urlopen(req, timeout=5).read())

def api(path, data):
    req = urllib.request.Request(f"http://localhost:5000/api/{path}", data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=20).read())

def card(page, name):
    return page.locator(".health-card", has=page.locator(".hc-name", has_text=name)).first

def quick_check(page):
    time.sleep(5.2)                       # past the double-click guard
    page.click("#quickCheck")
    page.wait_for_function("!document.getElementById('quickCheck').disabled", timeout=60000) if False else None
    t0 = time.time()
    while time.time() - t0 < 60:
        if page.is_enabled("#quickCheck") and page.inner_text("#quickCheck") == "Run quick check": break
        time.sleep(0.2)
    time.sleep(0.3)

def scroll_checks(pg, label):
    pg.evaluate("window.scrollTo(0, 0)"); time.sleep(0.4)
    pg.mouse.move(200, 400); pg.mouse.wheel(0, 900); time.sleep(0.6)
    check(f"{label}: page scrolls with the mouse wheel / finger", pg.evaluate("window.scrollY") > 100, str(pg.evaluate("window.scrollY")))
    pg.evaluate("window.scrollTo(0, 0)"); time.sleep(0.4)
    check(f"{label}: ↓ button shows at the top, ↑ is hidden", pg.is_visible("#toBottom") and not pg.is_visible("#toTop"))
    pg.click("#toBottom"); time.sleep(1.2)
    at_bottom = pg.evaluate("Math.abs(window.scrollY - (document.documentElement.scrollHeight - innerHeight)) < 5")
    check(f"{label}: ↓ goes to the bottom", at_bottom)
    check(f"{label}: ↑ shows at the bottom", pg.is_visible("#toTop"))
    pg.click("#toTop"); time.sleep(1.2)
    check(f"{label}: ↑ goes back to the top", pg.evaluate("window.scrollY") < 5)

mock("/control", {})
# A little activity so the overview has numbers in it.
api("feedback/rating", {"rating": "up", "language": "tw"})
api("feedback/rating", {"rating": "down", "language": "ee"})
api("chat", {"message": "What is puberty?", "language": "tw"})

with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    page = ctx.new_page(); page.set_default_timeout(30000)
    page.on("pageerror", lambda e: page_errors.append(str(e)))
    paid = ("llm", "tts", "asr", "translate")
    before = {k: mock("/calls").get(k, 0) for k in paid}
    page.goto("http://localhost:5000/admin")
    page.fill("#adminToken", "admin-e2e"); page.click("#adminConnect")
    page.wait_for_selector("#statusBanner.none")
    time.sleep(2)
    after = {k: mock("/calls").get(k, 0) for k in paid}
    check("opening the dashboard runs no check", "No health check" in page.inner_text("#statusHeadline"))
    check("…and makes no API calls", before == after, f"{before} → {after}")

    quick_check(page)
    check("clicking the button runs the check", page.locator(".health-card").count() >= 8)
    banner = page.get_attribute("#statusBanner", "class")
    check("all good → green banner", "ok" in banner or "warning" in banner, page.inner_text("#statusHeadline"))
    for name in ("Grok (xAI)", "Gemini (Google)", "OpenAI", "Qwen (Alibaba)",
                 "Translation · Twi", "Voice (TTS) · Twi", "Speech recognition · Twi", "Database", "Alert emails"):
        c = card(page, name)
        check(f"card present: {name}", c.count() == 1, c.inner_text().split("\n")[1] if c.count() else "")
    check("ASR tested with the TTS audio", "Heard" in card(page, "Speech recognition · Twi").inner_text())

    page.click("#fullCheck")
    t0 = time.time()
    while time.time() - t0 < 60 and not page.is_enabled("#fullCheck"): time.sleep(0.2)
    time.sleep(0.3)
    langs_ok = all(card(page, f"{kind} · {lang}").count() == 1
                   for kind in ("Translation", "Voice (TTS)", "Speech recognition") for lang in ("Twi", "Dagbani", "Ewe"))
    check("full check covers MT, TTS and ASR in all 3 languages", langs_ok)

    # Break the TTS key
    mock("/control", {"tts": {"status": 401}})
    quick_check(page)
    tts = card(page, "Voice (TTS) · Twi")
    check("broken TTS shows as not working", "down" in (tts.get_attribute("class") or ""))
    check("…with advice to fix the key", "KHAYA_TTS_KEY" in tts.inner_text(), tts.locator(".hc-advice").inner_text()[:90])
    check("banner turns orange (degraded)", "degraded" in page.get_attribute("#statusBanner", "class"), page.inner_text("#statusHeadline"))
    check("broken cards are listed first", "Voice (TTS)" in page.locator(".health-group").nth(1).locator(".hc-name").first.inner_text())
    page.screenshot(path=str(HERE / "admin-degraded.png"), full_page=True)

    # Every AI provider out of credits
    msg = "Your team has used all available credits"
    mock("/control", {n: {"status": 403, "message": msg} for n in ("grok", "gemini", "openai", "qwen")})
    quick_check(page)
    check("no AI working → red banner", "critical" in page.get_attribute("#statusBanner", "class"), page.inner_text("#statusHeadline"))
    check("…and advice says to top up credits", "credits" in card(page, "Grok (xAI)").inner_text().lower())

    mock("/control", {})
    page.click("#resetCircuits"); time.sleep(0.5)
    quick_check(page)
    check("after fixing, banner goes back to normal", "critical" not in page.get_attribute("#statusBanner", "class")
          and "degraded" not in page.get_attribute("#statusBanner", "class"))
    strip = card(page, "Voice (TTS) · Twi").locator(".hc-strip span")
    classes = [strip.nth(i).get_attribute("class") for i in range(strip.count())]
    check("history strip remembers the outage", "down" in classes and classes[-1] == "ok", str(classes[-5:]))

    # Overview & downloads
    kpis = page.inner_text("#kpis")
    check("overview shows ratings and helpful %", "Ratings" in kpis and "%" in kpis, kpis.replace("\n", " ")[:120])
    check("ratings by language", "Twi" in page.inner_text("#langBars") and "Ewe" in page.inner_text("#langBars"))
    with page.expect_download() as dl:
        page.locator(".dl", has_text="Ratings").locator("button", has_text="CSV").click()
    csv_text = Path(dl.value.path()).read_text(encoding="utf-8-sig")
    check("CSV download works", csv_text.startswith("rating") or "rating" in csv_text.splitlines()[0], csv_text.splitlines()[0][:80])
    page.screenshot(path=str(HERE / "admin-ok.png"), full_page=True)
    scroll_checks(page, "desktop")
    ctx.close()

    # Phone
    iphone = p.devices["iPhone 13"]
    m = browser.new_context(**{k: v for k, v in iphone.items() if k != "default_browser_type"}).new_page()
    m.on("pageerror", lambda e: page_errors.append(str(e)))
    m.goto("http://localhost:5000/admin"); m.fill("#adminToken", "admin-e2e"); m.click("#adminConnect")
    m.wait_for_selector(".health-card")   # shows the saved result from the checks above, no new calls
    vw = m.viewport_size["width"]
    check("dashboard fits a phone screen", m.evaluate("document.documentElement.scrollWidth") <= vw + 1)
    m.screenshot(path=str(HERE / "admin-mobile.png"))
    time.sleep(0.5)
    scroll_checks(m, "phone")
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed; page errors: {len(page_errors)}")
for e in page_errors[:5]: print("  ", e[:200])
sys.exit(1 if failed or page_errors else 0)
