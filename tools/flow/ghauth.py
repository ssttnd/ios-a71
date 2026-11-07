"""Authenticated GitHub API reads. The token stays in memory, is never printed or
written, and is sent only to api.github.com (not to the storage host a redirect names)."""
import json, subprocess, urllib.error, urllib.request

API = "https://api.github.com/repos/ssttnd/ios-a71"


def _token():
    p = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True, env={**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0"})
    for line in p.stdout.splitlines():
        if line.startswith("password="):
            return line[9:]
    raise SystemExit("no stored GitHub credential")


TOKEN = _token()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def get(path, binary=False):
    req = urllib.request.Request(API + path, headers={"User-Agent": "ios-a71-check", "Accept": "application/vnd.github+json", "Authorization": "Bearer " + TOKEN})
    op = urllib.request.build_opener(NoRedirect)
    try:
        data = op.open(req, timeout=120).read()
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308):
            raise
        loc = e.headers["Location"]
        data = urllib.request.urlopen(urllib.request.Request(loc, headers={"User-Agent": "ios-a71-check"}), timeout=600).read()
    return data if binary else json.loads(data)
