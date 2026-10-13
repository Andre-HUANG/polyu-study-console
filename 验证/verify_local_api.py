"""Real loopback HTTP regression with disposable synthetic account data."""
from http.cookiejar import CookieJar
import json
import secrets
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "学习控制台"))
import server
from local_access import LocalAccess
from native_canvas import save_private


def guard(event, args):
    if event in {"socket.connect", "socket.bind"}:
        if not isinstance(args[1], tuple) or args[1][0] != "127.0.0.1": raise RuntimeError("Only loopback permitted")
    if event == "socket.getaddrinfo" and args[0] != "127.0.0.1": raise RuntimeError("External DNS blocked")


def main():
    sys.addaudithook(guard)
    results = []
    original_csrf = server.CSRF
    with tempfile.TemporaryDirectory(prefix="polyu-api-security-") as directory:
        profile = Path(directory) / "account"
        with patch.multiple(server, PROJECT=profile.parent, PROFILE=profile, ACCESS=LocalAccess(),
                            NATIVE=True, NATIVE_SECRET=secrets.token_urlsafe(48), DIAGNOSTICS=None):
            http = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            worker = threading.Thread(target=http.serve_forever, daemon=True); worker.start()
            url = "http://127.0.0.1:" + str(http.server_port) + "/"
            public = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            private = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(CookieJar()))
            def request(opener, path, body=None, headers=None, status=200):
                supplied = {"Content-Type": "application/json", **(headers or {})}
                data = json.dumps(body).encode() if body is not None else None
                req = urllib.request.Request(url + path, data=data, headers=supplied)
                try:
                    with opener.open(req, timeout=10) as response:
                        raw = response.read(); actual = response.status
                        kind = response.headers.get("Content-Type", "")
                        value = json.loads(raw) if "application/json" in kind else raw
                        response_headers = dict(response.headers)
                except urllib.error.HTTPError as error:
                    actual = error.code; value = json.load(error); response_headers = {}
                assert actual == status, (path, actual, status)
                results.append({"operation": path.split("?")[0], "expectedHTTP": status, "result": "PASS"})
                return value, response_headers
            try:
                for path in ["api/data", "api/status", "api/documents", "api/timetable?date=2026-10-13", "api/calendar?month=2026-10", "api/documents/search?q=synthetic", "api/diagnostics"]:
                    request(public, path, status=401)
                page, _ = request(public, "")
                assert server.CSRF.encode() not in page and server.ACCESS.token.encode() not in page
                request(public, "api/settings", {"autoSync": False}, status=403)
                native_headers = {"X-PolyU-Native": server.NATIVE_SECRET}
                ticket, _ = request(public, "api/native/bootstrap", {}, native_headers)
                headers = {"Origin": url[:-1]}
                credentials, response_headers = request(private, "api/client/session", {"ticket": ticket["ticket"]}, headers)
                assert "HttpOnly" in response_headers["Set-Cookie"] and "SameSite=Strict" in response_headers["Set-Cookie"]
                request(public, "api/client/session", {"ticket": ticket["ticket"]}, headers, 403)
                data, _ = request(private, "api/data"); assert data["courseCount"] == 0
                headers["X-PolyU-Console"] = credentials["csrf"]
                request(private, "api/settings", {"autoSync": False}, headers)
                request(private, "api/data", headers={"Origin": "https://attacker.invalid"}, status=403)
                request(private, "api/data", headers={"Host": "attacker.invalid"}, status=403)
                request(public, "api/data", headers={"Cookie": server.ACCESS.cookie_name(http.server_port) + "=invalid"}, status=401)
                save_private(profile/"login.json", {"user_id": "synthetic-A", "name": "Synthetic A"})
                save_private(profile/"config.json", {"user": {"id": "synthetic-A"}, "courses": []})
                data, _ = request(private, "api/data"); assert data["connection"]["name"] == "Synthetic A"
                save_private(profile/"config.json", {"user":{"id":"synthetic-A"},"courses":[{"id":"1","code":"SYNTHETIC"}]})
                request(private,"api/timetable",{"action":"save","courseId":"1","title":"Synthetic lecture","kind":"lecture","date":"2026-10-13","time":"18:30","endTime":"20:30"},headers)
                timetable, _ = request(private,"api/timetable?date=2026-10-13")
                assert len(timetable["events"])==1
                request(private,"api/timetable",{"action":"delete","id":timetable["events"][0]["id"]},{"Origin":"https://attacker.invalid"},403)
                request(private,"api/timetable",{"action":"delete","id":timetable["events"][0]["id"]},{"Origin":url[:-1]},403)
                status, _ = request(private,"api/status");assert "revision" in status and "courses" not in status
                slim, slim_headers = request(private,"api/data?view=dashboard")
                assert "records" not in slim.get("documents",{})
                request(private,"api/data",headers={"If-None-Match":slim_headers["ETag"]})
                # The ETag path must return no body, preserving full data compatibility.
                conditional=urllib.request.Request(url+"api/data?view=dashboard",headers={"If-None-Match":slim_headers["ETag"]})
                try: private.open(conditional)
                except urllib.error.HTTPError as error:assert error.code==304 and not error.read()
                else:raise AssertionError("Unchanged data did not return 304")
                request(public, "api/native/logout", {}, native_headers)
                request(private, "api/data", status=401)
                save_private(profile/"login.json", {"user_id": "synthetic-B", "name": "Synthetic B"})
                save_private(profile/"config.json", {"user": {"id": "synthetic-B"}, "courses": []})
                ticket, _ = request(public, "api/native/bootstrap", {}, native_headers)
                request(private, "api/client/session", {"ticket": ticket["ticket"]}, {"Origin": url[:-1]})
                data, _ = request(private, "api/data")
                assert data["connection"]["name"] == "Synthetic B" and "Synthetic A" not in json.dumps(data)
                previous = server.ACCESS.token; server.ACCESS = LocalAccess()
                request(private, "api/data", status=401)
                assert server.ACCESS.token != previous
            finally:
                http.shutdown(); http.server_close(); worker.join(timeout=10); server.CSRF = original_csrf
    report = {"ok": True, "checks": results, "realCanvasValidated": False,
              "externalNetworkDisabled": True, "sameOSUserMalwareIsolation": False}
    (ROOT/"验证/local-api-evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "actualHTTPChecks": len(results), "realCanvasValidated": False}))


if __name__ == "__main__": main()
