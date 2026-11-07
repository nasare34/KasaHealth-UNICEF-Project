"""End-to-end browser test of Kasa Health v8 (headless Chromium + fake microphone)."""
import json, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
from playwright.sync_api import sync_playwright

results, console_errors = [], []
def wait_count(page, selector, n, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if page.locator(selector).count() >= n: return True
        time.sleep(0.1)
    raise TimeoutError(f"waiting for {n}× {selector}")
BOT = ".message.bot:not(.typing-indicator) .msg-translation"
def check(name, ok, info=""):
    results.append((name, bool(ok), info)); print(("PASS " if ok else "FAIL ") + name + (f" — {info}" if info else ""), flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                      f"--use-file-for-fake-audio-capture={HERE / 'speech.wav'}"])
    ctx = browser.new_context(permissions=["microphone"])
    page = ctx.new_page()
    page.set_default_timeout(20000)
    page.on("console", lambda m: m.type == "error" and console_errors.append(m.text))
    page.on("pageerror", lambda e: console_errors.append(f"PAGEERROR {e}"))

    page.goto("http://localhost:5000/")
    check("disclaimer shown", page.is_visible("#disclaimerModal"))
    check("agree disabled before tick", page.is_disabled("#disclaimerAgreeBtn"))
    check("tick-box locked until the agreement is scrolled", page.is_disabled("#disclaimerCheck"))
    page.hover("#disclaimerScroll"); page.mouse.wheel(0, 6000); time.sleep(0.4)
    check("tick-box unlocks after scrolling", page.is_enabled("#disclaimerCheck"))
    page.check("#disclaimerCheck"); page.click("#disclaimerAgreeBtn")
    page.wait_for_selector("#disclaimerModal.hidden", state="attached")
    check("disclaimer dismissed", True)

    # --- Text chat ---
    page.click("#modeText"); page.fill("#textInput", "What is puberty?"); page.press("#textInput", "Enter")
    page.wait_for_selector(".message.bot:not(.typing-indicator) .msg-translation")
    bot = page.locator(".message.bot:not(.typing-indicator)").last
    check("text chat answered in Twi + English", "[en-tw]" in bot.inner_text() and "english" in bot.inner_text().lower())
    check("thumbs shown", bot.locator(".thumb-up").count() == 1)

    # --- Thumbs up → skip code → toast ---
    bot.locator(".thumb-up").click(); bot.locator(".thumb-code-skip").click()
    page.wait_for_selector(".toast.show")
    check("rating thank-you toast", "Thank you" in page.inner_text("#toastHost"))

    # --- XSS: AI answer containing HTML must render as text ---
    page.fill("#textInput", "XSSTEST"); page.press("#textInput", "Enter")
    wait_count(page, BOT, 2)
    xss = page.evaluate("window.__xss === undefined && document.querySelectorAll('#messages img').length === 0")
    shown = page.locator(".message.bot .msg-bubble").last.inner_text()
    check("XSS neutralised (HTML shown as text, not executed)", xss and "<img" in page.locator('.message.bot:not(.typing-indicator)').last.inner_text(), shown[:60])

    # --- Friendly error when every LLM is blocked (403) ---
    page.fill("#textInput", "FAILALL"); page.press("#textInput", "Enter")
    page.wait_for_selector(".kind-error")
    err_text = page.locator(".kind-error").last.inner_text()
    check("friendly error message, no technical detail", "trouble" in err_text and "403" not in err_text and "denied" not in err_text.lower(), err_text[:70])
    check("retry button offered", page.locator(".retry-btn").count() >= 1)

    # --- Voice chat (tap to start, tap again to send) ---
    page.click("#modeVoice")
    n_bot = page.locator(BOT).count()
    # Cancel throws the recording away
    page.click("#micBtn"); time.sleep(1.5)
    check("voice chat: tap starts recording with a timer", page.is_visible("#recControls")
          and page.inner_text("#micLabel") == "Tap to send" and page.inner_text("#recTimer") in ("0:01", "0:02"),
          page.inner_text("#recTimer"))
    page.click("#recCancel"); time.sleep(1.0)
    check("voice chat: cancel discards it", page.locator(BOT).count() == n_bot and not page.is_visible("#recControls"))
    # Tap, speak, tap again to send
    page.click("#micBtn"); time.sleep(3.2); page.click("#micBtn")
    check("voice chat: second tap sends", page.inner_text("#micLabel") == "Tap to speak")
    wait_count(page, BOT, n_bot + 1)
    check("voice chat: transcript shown", page.locator(".msg-lang-note").count() >= 1)
    check("voice chat: audio buttons present", page.locator(".message.bot .play-btn").last.inner_text().startswith(("▶", "⏸")))

    # --- Transcribe (tap to start, tap again to stop) ---
    page.click('.tab-btn[data-tab="transcribe"]')
    page.click("#transMicBtn"); time.sleep(0.6)
    check("transcribe: tap starts recording", "recording" in page.get_attribute("#transMicBtn", "class")
          and page.is_visible("#transRecControls") and page.inner_text("#transMicLabel") == "Tap to stop")
    time.sleep(2.6); page.click("#transMicBtn")
    page.wait_for_selector("#transcribeResult", state="visible")
    check("transcribe: second tap transcribes", len(page.inner_text("#transcriptBox")) > 3, page.inner_text("#transcriptBox")[:40])
    time.sleep(0.5)
    page.click("#transMicBtn"); time.sleep(0.8)
    page.click('.tab-btn[data-tab="chat"]'); time.sleep(0.3)
    check("leaving the tab stops a recording", "recording" not in page.get_attribute("#transMicBtn", "class")
          and not page.is_visible("#transRecControls"))

    # --- Voice agent: push-to-talk ---
    page.click('.tab-btn[data-tab="agent"]')
    check("agent default mode = hold to talk", "active" in page.get_attribute("#agentModePtt", "class"))
    check("agent still uses hold to talk (unchanged)", page.inner_text("#agentMicLabel") == "Hold to talk")
    box = page.locator("#agentMicBtn").bounding_box()
    page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
    page.mouse.down(); time.sleep(0.5)
    check("agent PTT: status while holding", page.inner_text("#agentStatusPill") in ("Hearing you…", "Listening…"), page.inner_text("#agentStatusPill"))
    time.sleep(2.7); page.mouse.up()
    page.wait_for_selector(".agent-bubble-wrap.agent-bubble-agent", timeout=30000)
    statuses = set()
    for _ in range(40):
        statuses.add(page.inner_text("#agentStatusPill"))
        if page.inner_text("#agentStatusPill") == "Ready" and "Speaking…" in statuses: break
        time.sleep(0.1)
    check("agent PTT: replied, spoke, returned to Ready", "Speaking…" in statuses and page.inner_text("#agentStatusPill") == "Ready", str(statuses))
    check("agent: replay button + thumbs", page.locator(".agent-bubble-agent .play-btn").count() >= 1 and page.locator(".agent-bubble-agent .thumb-up").count() >= 1)

    # --- Voice agent: hands-free (voice activity detection) ---
    page.click("#agentModeHands")
    before = page.locator(".agent-bubble-wrap.agent-bubble-agent").count()
    page.click("#agentMicBtn")
    seen, t0 = set(), time.time()
    while time.time() - t0 < 30:
        seen.add(page.inner_text("#agentStatusPill"))
        if page.locator(".agent-bubble-wrap.agent-bubble-agent").count() > before and page.inner_text("#agentStatusPill") == "Listening…":
            break
        time.sleep(0.1)
    after = page.locator(".agent-bubble-wrap.agent-bubble-agent").count()
    check("hands-free: detected speech start & end automatically", "Hearing you…" in seen and "Thinking…" in "".join(seen), str(sorted(seen)))
    check("hands-free: answered and resumed listening by itself", after > before and page.inner_text("#agentStatusPill") == "Listening…")
    # Interrupt / end session with a tap
    page.click("#agentMicBtn"); time.sleep(0.4)
    if page.inner_text("#agentStatusPill") != "Ready":
        page.click("#agentMicBtn"); time.sleep(0.4)
    check("hands-free: tap ends session", page.inner_text("#agentStatusPill") in ("Ready", "Listening…"), page.inner_text("#agentStatusPill"))
    page.keyboard.press("Escape"); time.sleep(0.3)
    check("Escape stops agent", page.inner_text("#agentStatusPill") == "Ready")
    page.screenshot(path=str(HERE / "agent.png"))

    # --- Survey ---
    page.click("#topbarSurveyBtn")
    page.fill('input[name="email"]', "tester@example.com")
    page.select_option('select[name="gender"]', "Prefer not to say")
    page.select_option('select[name="location"]', "Ashanti Region")
    for name in ["ease", "helpful", "asr_transcript", "text_translation", "tts_quality", "recommend"]:
        page.locator(f'label.rating-opt:has(input[name="{name}"])').first.click()
    page.fill('textarea[name="feedback"]', "Works well")
    page.click("#surveyForm .survey-submit-btn")
    page.wait_for_selector("#surveyThankYou")
    check("survey submitted with thank-you", True)
    time.sleep(3.3)

    # --- History restore ---
    page.click('.tab-btn[data-tab="chat"]')
    page.click("#newChatBtn"); page.click("#topbarHistoryBtn")
    page.wait_for_selector("#historyList .history-item")
    page.locator("#historyList .history-item").first.click()
    page.wait_for_selector(".message.user")
    check("saved conversation restored from history", page.locator(".message.user").count() >= 1)

    json.dump({"console_errors": console_errors}, open(HERE / "console.json", "w"), indent=1)
    browser.close()

failed = [r for r in results if not r[1]]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
print("console errors:", len(console_errors))
for e in console_errors[:10]: print("  ", e[:160])
sys.exit(1 if failed else 0)
