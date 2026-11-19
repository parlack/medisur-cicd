"""Run with sudo on the tools VM; secret values never leave server files."""
import base64
import http.cookiejar
import http.client
import json
import os
from pathlib import Path
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request


SECRET_DIR = Path("/etc/medisur/secrets")


def save_secret(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(value + "\n")


def client(user, password):
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    )
    credential = base64.b64encode(f"{user}:{password}".encode()).decode()
    opener.addheaders = [("Authorization", "Basic " + credential)]
    return opener


def request(opener, url, data=None, headers=None):
    payload = None if data is None else urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=payload, headers=headers or {})
    with opener.open(req, timeout=30) as response:
        return response.read().decode()


def wait_ready(url, predicate):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for attempt in range(120):
        try:
            if predicate(request(opener, url)):
                return
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.RemoteDisconnected):
            pass
        time.sleep(5)
    raise RuntimeError("Service readiness timeout: " + url)


def provision_sonar():
    base = "http://127.0.0.1:9000"
    wait_ready(base + "/api/system/status", lambda body: json.loads(body)["status"] == "UP")
    path = SECRET_DIR / "sonar_admin_password"
    if not path.exists():
        save_secret(path, secrets.token_urlsafe(40) + "Aa1!")
    os.chown(path, 0, 0)
    os.chmod(path, 0o400)
    password = path.read_text().strip()
    current = client("admin", password)
    valid = json.loads(request(current, base + "/api/authentication/validate"))["valid"]
    if not valid:
        initial = client("admin", "admin")
        try:
            request(initial, base + "/api/users/change_password", {
                "login": "admin", "previousPassword": "admin", "password": password,
            })
        except urllib.error.HTTPError as error:
            detail = error.read().decode().replace(password, "[REDACTED]")
            raise RuntimeError("Sonar password rotation failed: " + detail) from None
    assert json.loads(request(current, base + "/api/authentication/validate"))["valid"]
    assert not json.loads(request(client("admin", "admin"), base + "/api/authentication/validate"))["valid"]
    print("Sonar administrator password rotated and verified; default password rejected.")


def provision_jenkins():
    base = "http://127.0.0.1:8080"
    wait_ready(base + "/login", lambda body: True)
    user = "medisur-admin"
    path = SECRET_DIR / "jenkins_api_token"
    if not path.exists():
        password = (SECRET_DIR / "jenkins_admin_password").read_text().strip()
        opener = client(user, password)
        crumb = json.loads(request(opener, base + "/crumbIssuer/api/json"))
        script = '''
import hudson.model.User
import jenkins.security.ApiTokenProperty
def user = User.getById("medisur-admin", false)
def property = user.getProperty(ApiTokenProperty.class)
def token = property.tokenStore.generateNewToken("medisur-main-automation")
user.save()
print(token.plainValue)
'''
        token = request(opener, base + "/scriptText", {"script": script}, {
            crumb["crumbRequestField"]: crumb["crumb"],
        }).strip()
        if not token.isalnum() or len(token) != 34:
            raise RuntimeError("Jenkins did not return a valid API token; response suppressed")
        save_secret(path, token)
    os.chown(path, 0, 0)
    os.chmod(path, 0o400)
    opener = client(user, path.read_text().strip())
    identity = json.loads(request(opener, base + "/whoAmI/api/json"))
    assert identity["authenticated"] and identity["name"] == user
    print("Jenkins API token saved root-only and authenticated successfully.")


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("Run with sudo on the tools VM.")
    provision_sonar()
    provision_jenkins()