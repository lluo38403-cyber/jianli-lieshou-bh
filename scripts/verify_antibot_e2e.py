"""Local end-to-end check for the anti-detection layer.

Starts an ISOLATED Chrome for Testing (headless) on debug port 9333 with
--disable-blink-features=AutomationControlled, plus its own Browser Runtime
proxy on port 3457 that only ever probes 9333. It never touches port 9222
(the TG residential Chrome) or the running proxy on 3456.

Checks, against real pages in a real browser:
  1. navigator.webdriver is undefined (no automation flag on a CDP-attached
     real Chrome with the stealth launch arg).
  2. human_browse() actually scrolls the page in random steps.
  3. CAPTCHA_DETECT_JS reports null on a clean job page and 'captcha' on a
     slider-verification fixture page.
  4. Micro-delays sampled live stay inside 3-8s.

Usage (from the repo root):
    PYTHONPATH=src .venv/bin/python scripts/verify_antibot_e2e.py
"""

import json
import random
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

CHROME = Path.home() / "workspace/proxied-browser-test/chrome-linux64/chrome"
CHROME_PORT = 9333
PROXY_PORT = 3457

CLEAN_PAGE = """<!doctype html><html><head><title>高级 Java 工程师</title></head><body>
<h1>高级 Java 工程师</h1><button>立即沟通</button><button>投递简历</button>
<div style="height:4000px"><p>岗位描述：熟悉短信验证码接入流程。</p></div>
</body></html>"""

CAPTCHA_PAGE = """<!doctype html><html><head><title>安全验证</title></head><body>
<div class="geetest_panel"><p>请完成安全验证</p><p>拖动滑块完成验证</p></div>
</body></html>"""


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        raise SystemExit(1)


def main():
    from bosshunter import antibot
    from bosshunter.browser import (
        close_tab,
        configure,
        evaluate,
        new_tab,
        scroll,
    )
    from bosshunter.browser import runtime as browser_runtime

    tmp = Path(tempfile.mkdtemp(prefix="antibot-e2e-"))
    clean_path = tmp / "clean.html"
    captcha_path = tmp / "captcha.html"
    clean_path.write_text(CLEAN_PAGE, encoding="utf-8")
    captcha_path.write_text(CAPTCHA_PAGE, encoding="utf-8")

    chrome = subprocess.Popen(
        [
            str(CHROME),
            "--headless=new",
            "--no-sandbox",
            f"--remote-debugging-port={CHROME_PORT}",
            "--disable-blink-features=AutomationControlled",
            f"--user-data-dir={tmp / 'profile'}",
            "--no-first-run",
            "--disable-gpu",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        configure({"browser": {"proxy_port": PROXY_PORT, "chrome_ports": [CHROME_PORT]}})
        # Wait for the proxy to find the isolated Chrome (never 9222).
        target_id = None
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                target_id = new_tab(f"file://{clean_path}", background=True)
            except Exception:
                target_id = None
            if target_id:
                break
            time.sleep(1)
        check("proxy connected to the isolated Chrome", bool(target_id))

        # 1. No webdriver flag: the property is false (not true) on a
        # CDP-attached real Chrome. (Headless Chrome exposes the property
        # as a boolean false; headed Chrome leaves it undefined — both
        # mean "not driven by WebDriver".)
        webdriver_value = evaluate(target_id, "navigator.webdriver")
        check(
            "navigator.webdriver is false",
            webdriver_value is False,
            f"got {webdriver_value!r}",
        )
        check(
            "check_webdriver_flag() reports no flag",
            antibot.check_webdriver_flag(evaluate, target_id) in (None, False),
        )

        # 2. Human browsing really scrolls.
        steps: list[int] = []
        max_scroll_y = {"value": 0}
        stop_polling = threading.Event()

        def poll_scroll():
            while not stop_polling.is_set():
                try:
                    y = evaluate(target_id, "window.scrollY")
                    if isinstance(y, (int, float)):
                        max_scroll_y["value"] = max(max_scroll_y["value"], int(y))
                except Exception:
                    pass
                time.sleep(0.2)

        poller = threading.Thread(target=poll_scroll, daemon=True)
        poller.start()
        try:
            def recording_scroll(tid, y):
                steps.append(int(y))
                return scroll(tid, y)

            stopped = antibot.human_browse(
                target_id,
                None,
                min_s=8.0,
                max_s=12.0,
                scroll_fn=recording_scroll,
                rng=random.Random(20261119),
            )
        finally:
            stop_polling.set()
        check("human_browse was not stopped", stopped is False)
        check("human_browse scrolled in several steps", len(steps) >= 2, f"{len(steps)} steps")
        check("scroll steps vary", len(set(steps)) > 1, f"steps={steps}")
        check("page actually moved", max_scroll_y["value"] > 0, f"max scrollY={max_scroll_y['value']}")

        # 3. Detector: clean page -> null.
        clean = antibot.detect_page_risk(evaluate, target_id)
        check("detector clears the clean job page", clean.get("risk") is None, str(clean))

        # 4. Detector: captcha fixture -> captcha.
        captcha_target = new_tab(f"file://{captcha_path}", background=True)
        check("captcha fixture tab opened", bool(captcha_target))
        detected = antibot.detect_page_risk(evaluate, captcha_target)
        check(
            "detector flags the captcha fixture",
            detected.get("risk") == "captcha",
            str(detected),
        )

        # 5. Live micro-delay samples stay inside 3-8s.
        samples = [antibot.sample_micro_delay() for _ in range(50)]
        check(
            "live micro-delays within 3-8s",
            all(3.0 <= value <= 8.0 for value in samples),
            f"min={min(samples):.2f} max={max(samples):.2f}",
        )

        close_tab(captcha_target)
        close_tab(target_id)
        print("antibot e2e: all checks passed")
    finally:
        proc = browser_runtime._runtime_process
        if proc is not None:
            proc.terminate()
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except subprocess.TimeoutExpired:
            chrome.kill()


if __name__ == "__main__":
    main()
