"""Capture live, unmodified local-tunnel pages; never persist credentials.

Browser requirements (user-level setup):
    python -m pip install --user playwright
    python -m playwright install chromium
Run from any directory:
    python capture-evidence.py --key <absolute-private-key-path>
Requires existing SSH tunnels on ports 13000, 18080 and 19000, trusted SSH
host key, and passwordless sudo access to /etc/medisur/secrets on VM-TOOLS.
Only docs/evidence/*.png are written. No HTML, traces, logs or auth profiles.
"""

import argparse
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

# Playwright debugging can expose fill arguments, so disable it before import.
for variable in ("DEBUG", "PWDEBUG", "DEBUG_FILE"):
    os.environ.pop(variable, None)

from playwright.sync_api import sync_playwright


class EvidenceError(Exception):
    """Only fixed, non-secret diagnostic messages may leave the process."""


def origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port


def check_frames(page, base):
    if origin(page.url) != origin(base):
        raise EvidenceError("Unexpected top-level origin; capture stopped")
    for frame in page.frames:
        if frame.url == "about:blank":
            continue  # Empty inherited-origin frame, never targeted for input.
        if origin(frame.url) != origin(base):
            raise EvidenceError("Cross-origin frame blocked; approval required")


def password_from_ssh(key, filename):
    allowed = {"grafana_admin_password", "jenkins_admin_password", "sonar_admin_password"}
    if filename not in allowed:
        raise EvidenceError("Invalid secret identifier")
    result = subprocess.run(
        ["ssh", "-i", str(key), "-o", "BatchMode=yes", "-o",
         "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=12",
         "username@20.127.28.221", "sudo", "-n", "cat",
         "/etc/medisur/secrets/" + filename],
        capture_output=True, timeout=25,
    )
    if result.returncode or not result.stdout.strip():
        raise EvidenceError("SSH secret retrieval failed (output suppressed)")
    return result.stdout.decode().strip()


def navigate(page, base, path):
    page.goto(base + path, wait_until="domcontentloaded", timeout=30000)
    check_frames(page, base)


def settle(page, base, seconds=4):
    # A hard bound avoids indefinite network-idle waits on polling dashboards.
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(seconds * 1000)
    check_frames(page, base)


def login(page, base, path, username, password, user_selector):
    navigate(page, base, path)
    user = page.locator(user_selector).first
    user.wait_for(state="visible", timeout=15000)
    check_frames(page, base)
    user.fill(username)
    check_frames(page, base)
    page.locator('input[type="password"]').first.fill(password)
    check_frames(page, base)
    page.locator('button[type="submit"], input[type="submit"]').first.click()
    page.locator('input[type="password"]').first.wait_for(state="hidden", timeout=20000)
    settle(page, base)


def capture(page, base, destination, secrets):
    check_frames(page, base)
    for frame in page.frames:
        for field in frame.locator('input[type="password"]').all():
            if field.is_visible():
                raise EvidenceError("Visible password field: screenshot refused")
        # Check both rendered text and visible form values without logging them.
        visible = frame.locator("body").inner_text(timeout=5000)
        values = frame.locator("input:visible, textarea:visible").evaluate_all(
            "elements => elements.map(element => element.value || '').join('\\n')"
        )
        if any(secret and (secret in visible or secret in values) for secret in secrets):
            raise EvidenceError("Visible credential: screenshot refused")
    page.screenshot(path=str(destination), full_page=False, timeout=15000)
    print("CAPTURE " + str(destination), flush=True)


def api(page, base, path):
    check_frames(page, base)
    response = page.request.get(base + path, timeout=15000, max_redirects=0)
    if not response.ok:
        raise EvidenceError("Same-origin status API unavailable")
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--only", choices=["grafana", "jenkins", "sonar"])
    args = parser.parse_args()
    output = Path(__file__).resolve().parents[1] / "docs" / "evidence"
    output.mkdir(parents=True, exist_ok=True)
    failures = []
    started = time.monotonic()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for service, port, username in [
            ("grafana", 13000, "medisur-admin"),
            ("jenkins", 18080, "medisur-admin"),
            ("sonar", 19000, "admin"),
        ]:
            if args.only and service != args.only:
                continue
            if time.monotonic() - started > 420:
                raise EvidenceError("Capture time budget exhausted")
            base = "http://127.0.0.1:" + str(port)
            context = browser.new_context(
                viewport={"width": 1600, "height": 1000},
                color_scheme="light", device_scale_factor=1,
                service_workers="block", accept_downloads=False,
            )
            # Never follow links, load scripts, or send credentials off-origin.
            def restrict_origin(route):
                if origin(route.request.url) == origin(base):
                    route.continue_()
                else:
                    route.abort()

            context.route("**/*", restrict_origin)
            page = context.new_page()
            page.set_default_timeout(15000)
            try:
                password = password_from_ssh(args.key, service + "_admin_password")
                if service == "grafana":
                    login(page, base, "/login", username, password,
                          'input[name="user"], input[autocomplete="username"]')
                    for uid in ("medisur-infrastructure", "medisur-application"):
                        navigate(page, base, "/d/" + uid +
                                 "?orgId=1&from=now-1h&to=now&timezone=browser&theme=light&refresh=10s")
                        settle(page, base, 18)
                        capture(page, base, output / ("grafana-" + uid + ".png"), [password])
                elif service == "jenkins":
                    login(page, base, "/login", username, password, 'input[name="j_username"]')
                    navigate(page, base, "/job/medisur-main/")
                    settle(page, base, 5)
                    capture(page, base, output / "jenkins-main-stages.png", [password])
                    selected = None
                    for number in (3, 2):
                        status = api(page, base, "/job/medisur-main/" + str(number) +
                                     "/api/json?tree=number,result,building")
                        print("Jenkins build " + str(number) + ": " + str(status.get("result")), flush=True)
                        if status.get("result") == "SUCCESS" and not status.get("building"):
                            selected = number
                            break
                    if selected is None:
                        raise EvidenceError("Neither main build 2 nor 3 is verified green")
                    navigate(page, base, "/job/medisur-main/" + str(selected) + "/")
                    settle(page, base)
                    capture(page, base, output / ("jenkins-main-build-" + str(selected) + ".png"), [password])
                    navigate(page, base, "/job/medisur-quality-demo/lastBuild/testReport/")
                    settle(page, base)
                    capture(page, base, output / "jenkins-quality-demo-tests.png", [password])
                else:
                    print("Sonar: opening login", flush=True)
                    login(page, base, "/sessions/new", username, password,
                          'input[name="login"], input[autocomplete="username"], input[type="text"]')
                    print("Sonar: authenticated", flush=True)
                    for project in ("medisur-quality-demo", "medisur-cicd"):
                        navigate(page, base, "/dashboard?id=" + project)
                        settle(page, base, 8)
                        for label in ("Later", "Got it", "Dismiss"):
                            control = page.get_by_role("button", name=label, exact=True)
                            if control.count() and control.first.is_visible():
                                check_frames(page, base)
                                print("Sonar: dismissing " + label, flush=True)
                                control.first.click()
                                page.wait_for_timeout(600)
                        overall = page.get_by_text("Overall Code", exact=True)
                        if overall.count():
                            check_frames(page, base)
                            overall.first.click()
                        settle(page, base, 1)
                        status = api(page, base, "/api/qualitygates/project_status?projectKey=" + project)
                        print("Sonar " + project + ": " +
                              str(status.get("projectStatus", {}).get("status")), flush=True)
                        capture(page, base, output / ("sonar-" + project + ".png"), [password])
                password = None
            except EvidenceError as error:
                failures.append(service)
                print("FAILED " + service + ": " + str(error), flush=True)
            except Exception as error:
                failures.append(service)
                # Playwright exception messages may include secret fill arguments.
                print("FAILED " + service + ": " + type(error).__name__ +
                      " (details suppressed for credential safety)", flush=True)
            finally:
                context.close()
        browser.close()
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print("Capture stopped: " + type(error).__name__ + " (private details suppressed)")
        raise SystemExit(1)