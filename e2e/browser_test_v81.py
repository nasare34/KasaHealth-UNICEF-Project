"""v8.1 browser checks: themes, sidebar, crisis support + email, admin switching, hedging, mobile."""
import json, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
results, console_errors = [], []
def check(name, ok, info=""):
    results.append((name, bool(ok))); print(("PASS " if ok else "FAIL ") + name + (f" — {info}" if info else ""), flush=True)
def mock(path, data=None):
    req = urllib.request.Request(f"http://127.0.0.1:9100{path}", data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"}, method="POST" if data is not None else "GET")
    return json.loads(urllib.request.urlopen(req, timeout=5).read())
def agree(page):
    page.wait_for_selector("#disclaimerScroll")
    page.hover("#disclaimerScroll"); page.mouse.wheel(0, 6000); time.sleep(0.4)
    page.check("#disclaimerCheck"); page.click("#disclaimerAgreeBtn"); time.sleep(0.4)
def last_bot(page):
    return page.locator(".message.bot:not(.typing-indicator)").last
def ask(page, text, n_before):
    page.fill("#textInput", text); page.press("#textInput", "Enter")
    t0 = time.time()
    while time.time() - t0 < 30:
        if page.locator(".message.bot:not(.typing-indicator) .msg-bubble").count() > n_before: return time.time() - t0
        time.sleep(0.1)
    raise TimeoutError(text)

mock("/control", {})
with sync_playwright() as p:
    browser = p.chromium.launch()
    # --- Desktop ---
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    page = ctx.new_page(); page.set_default_timeout(20000)
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR {e}"))
    page.goto("http://localhost:5000/"); agree(page)
    page.click("#modeText")

    # Theme
    start = page.get_attribute("html", "data-theme")
    page.click("#themeToggle"); flipped = page.get_attribute("html", "data-theme")
    page.reload(); agree(page) if page.is_visible("#disclaimerScroll") else None
    check("theme toggles and is remembered after reload", start != flipped and page.get_attribute("html", "data-theme") == flipped, f"{start}→{flipped}")
    page.screenshot(path=str(HERE / f"desktop-{flipped}.png"))
    page.click("#themeToggle")
    check("theme toggles back", page.get_attribute("html", "data-theme") == start)

    # Sidebar collapse (desktop)
    w_open = page.locator("#main").bounding_box()["width"]
    page.click("#menuBtn"); time.sleep(0.4)
    w_closed = page.locator("#main").bounding_box()["width"]
    check("sidebar hides and main area expands", w_closed > w_open + 200, f"{int(w_open)}→{int(w_closed)}px")
    page.reload(); time.sleep(0.4)
    check("sidebar stays hidden after reload", page.locator("#main").bounding_box()["width"] > w_open + 200)
    page.click("#menuBtn"); time.sleep(0.4)
    check("sidebar shows again", abs(page.locator("#main").bounding_box()["width"] - w_open) < 5)

    # AI-can-make-mistakes note
    page.click("#modeText")
    n = page.locator(".message.bot:not(.typing-indicator) .msg-bubble").count()
    ask(page, "What is puberty?", n)
    check("answer carries 'AI can make mistakes' note", "can make mistakes" in last_bot(page).inner_text())

    # Crisis flow
    emails_before = len(mock("/emails"))
    n = page.locator(".message.bot:not(.typing-indicator) .msg-bubble").count()
    ask(page, "I want to kill myself", n)
    bot = last_bot(page)
    check("crisis: support card with tap-to-call 112", bot.locator(".crisis-card a[href='tel:112']").count() == 1)
    check("crisis: reply mentions 112", "112" in bot.locator(".msg-bubble").first.inner_text())
    bot.locator(".crisis-form input[type=tel]").fill("024 123 4567")
    bot.locator(".crisis-form input[type=text]").first.fill("Ama")
    bot.locator(".crisis-submit").click(); time.sleep(0.4)
    check("crisis: consent required before sending", bot.locator(".crisis-form").count() == 1)
    bot.locator(".crisis-form .consent input").check(); bot.locator(".crisis-submit").click()
    page.wait_for_selector(".crisis-done")
    check("crisis: thank-you shown after sending", "Thank you" in bot.locator(".crisis-done").inner_text())
    time.sleep(1.0)
    emails = mock("/emails")[emails_before:]
    subjects = [e["subject"] for e in emails]
    check("crisis: safety alert email sent", any("Safety alert" in s for s in subjects), str(subjects))
    urgent = [e for e in emails if "URGENT" in e["subject"]]
    check("crisis: contact email has name + phone, to akwasi@khaya.ai",
          bool(urgent) and "024 123 4567" in urgent[0]["text"] and "Ama" in urgent[0]["text"] and urgent[0]["to"] == ["akwasi@khaya.ai"])

    # Hedging: Grok slow → Gemini answers quickly
    mock("/control", {"grok": {"delay": 8}})
    n = page.locator(".message.bot:not(.typing-indicator) .msg-bubble").count()
    took = ask(page, "Tell me about menstruation", n)
    check("slow provider is bypassed quickly (hedging)", took < 5 and "(gemini)" in last_bot(page).inner_text(), f"{took:.1f}s")

    # Out of credits → instant failover + error email
    mock("/control", {"grok": {"status": 403, "message": "Your team has used all available credits"}})
    emails_before = len(mock("/emails"))
    n = page.locator(".message.bot:not(.typing-indicator) .msg-bubble").count()
    took = ask(page, "What is contraception?", n)
    time.sleep(1.0)
    check("provider out of credits → next provider answers", "(gemini)" in last_bot(page).inner_text(), f"{took:.1f}s")
    check("provider-down alert emailed", any("provider_down:grok" in e["subject"] for e in mock("/emails")[emails_before:]))
    mock("/control", {})

    # Admin page: reorder providers
    admin = ctx.new_page(); admin.goto("http://localhost:5000/admin")
    admin.fill("#adminToken", "wrong"); admin.click("#adminConnect"); time.sleep(0.6)
    check("admin rejects a wrong token", admin.is_hidden("#llmCard"))
    admin.fill("#adminToken", "admin-e2e"); admin.click("#adminConnect")
    admin.wait_for_selector("#llmRows tr")
    names = admin.locator("#llmRows tr td:nth-child(2)").all_inner_texts()
    check("admin lists providers", "Grok (xAI)" in names and "Gemini (Google)" in names and "Qwen (Alibaba)" in names, str(names))
    # Move Qwen to the top: click its ▲ until first
    for _ in range(6):
        rows = admin.locator("#llmRows tr")
        idx = [r for r in range(rows.count()) if "Qwen" in rows.nth(r).inner_text()][0]
        if idx == 0: break
        rows.nth(idx).locator("button[aria-label='Move up']").click()
    admin.click("#saveOrder"); time.sleep(0.6)
    admin.click("#resetCircuits"); time.sleep(0.5)
    admin.screenshot(path=str(HERE / "admin.png"), full_page=True)
    time.sleep(5.5)   # other workers pick the new order up within ~5 s
    n = page.locator(".message.bot:not(.typing-indicator) .msg-bubble").count()
    ask(page, "How do STIs spread?", n)
    check("admin order change takes effect (Qwen answers)", "(qwen)" in last_bot(page).inner_text())
    admin.click("#saveOrder") if False else None
    urllib.request.urlopen(urllib.request.Request("http://localhost:5000/api/admin/llm", method="POST",
        data=json.dumps({"order": ["grok", "gemini", "openai", "qwen"], "disabled": []}).encode(),
        headers={"Content-Type": "application/json", "X-Admin-Token": "admin-e2e"}), timeout=5)
    ctx.close()

    # --- Mobile (iPhone-sized) ---
    iphone = p.devices["iPhone 13"]
    mctx = browser.new_context(**{k: v for k, v in iphone.items() if k != "default_browser_type"})
    m = mctx.new_page(); m.set_default_timeout(20000)
    m.on("pageerror", lambda e: console_errors.append(f"PAGEERROR mobile {e}"))
    m.goto("http://localhost:5000/")
    m.screenshot(path=str(HERE / "mobile-agreement.png"))
    check("mobile: agreement fits the screen", m.locator(".disclaimer-card").bounding_box()["height"] <= 844 + 1)
    agree(m)
    vw = m.viewport_size["width"]
    sw = m.evaluate("document.documentElement.scrollWidth")
    check("mobile: no sideways scrolling", sw <= vw + 1, f"{sw} vs {vw}")
    check("mobile: sidebar starts hidden", m.locator("#sidebar").bounding_box()["x"] < 0)
    m.click("#menuBtn"); time.sleep(0.4)
    check("mobile: menu opens drawer with backdrop", m.locator("#sidebar").bounding_box()["x"] >= 0 and m.is_visible("#sidebarBackdrop.show"))
    m.screenshot(path=str(HERE / "mobile-drawer.png"))
    m.mouse.click(vw - 20, 400); time.sleep(0.4)
    check("mobile: tapping outside closes drawer", m.locator("#sidebar").bounding_box()["x"] < 0)
    m.click('.tab-btn[data-tab="agent"]'); time.sleep(0.3)
    mb = m.locator("#agentMicBtn").bounding_box()
    check("mobile: agent mic button fully on screen & easy to press (≥ 44 px)", mb["y"] + mb["height"] <= 844 and mb["height"] >= 44, str(mb))
    ctl = m.locator(".agent-controls").bounding_box()["height"]
    check("mobile: agent controls are compact (≤ 130 px)", ctl <= 130, f"{ctl:.0f}px")
    m.screenshot(path=str(HERE / "mobile-agent.png"))
    m.click('.tab-btn[data-tab="chat"]'); m.click("#modeText")
    fs = m.evaluate("getComputedStyle(document.getElementById('textInput')).fontSize")
    check("mobile: input is 16px (no iOS zoom)", fs == "16px", fs)
    n = m.locator(".message.bot:not(.typing-indicator) .msg-bubble").count()
    ask(m, "What is puberty?", n)
    m.screenshot(path=str(HERE / "mobile-chat.png"))
    check("mobile: answer readable without sideways scroll", m.evaluate("document.documentElement.scrollWidth") <= vw + 1)
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed; page errors: {len(console_errors)}")
for e in console_errors[:5]: print("  ", e[:200])
sys.exit(1 if failed or console_errors else 0)
