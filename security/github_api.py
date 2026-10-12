"""Repository-scoped publishing transport. Never log or persist authentication."""
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
REPO="Andre-HUANG/polyu-study-console"
BASE="https://api.github.com/repos/"+REPO
_secret=None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        raise RuntimeError("Authenticated GitHub API requests must not redirect")


def token():
    global _secret
    if _secret:return _secret
    _secret=os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not _secret:
        env=os.environ.copy();env["GIT_TERMINAL_PROMPT"]="0"
        result=subprocess.run([os.environ.get("POLYU_GIT","git"),"credential","fill"],
            input="protocol=https\nhost=github.com\npath="+REPO+".git\n\n",capture_output=True,text=True,timeout=20,env=env)
        values=dict(line.split("=",1) for line in result.stdout.splitlines() if "=" in line)
        _secret=values.get("password")
    if not _secret:raise RuntimeError("No authorized GitHub credential for the target repository")
    return _secret


def request(path,method="GET",payload=None):
    if (not re.match(r"^/(?:actions/|releases(?:[/?]|$)|git/refs(?:[/?]|$))",path) or
        any(part in path for part in ("://","..","\\","%","\r","\n","#"))):
        raise ValueError("Endpoint outside the authorized repository workflow")
    data=json.dumps(payload).encode() if payload is not None else None
    req=urllib.request.Request(BASE+path,data=data,method=method,
        headers={"Authorization":"Bearer "+token(),"Accept":"application/vnd.github+json",
                 "Content-Type":"application/json","X-GitHub-Api-Version":"2022-11-28"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=60) as response:
            body=response.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as error:
        raise RuntimeError("GitHub repository API returned HTTP "+str(error.code)) from None


def dispatch(workflow,ref,inputs=None):
    if workflow not in {"ci.yml","release.yml"}:raise ValueError("Unknown project workflow")
    return request("/actions/workflows/"+workflow+"/dispatches","POST",{"ref":ref,"inputs":inputs or {}})


def upload(release_id,path):
    path=Path(path)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+",path.name):raise ValueError("Unsafe release asset name")
    url="https://uploads.github.com/repos/"+REPO+"/releases/"+str(int(release_id))+"/assets?name="+path.name
    # The verified upload endpoint is fixed, never read from repository content.
    req=urllib.request.Request(url,data=path.read_bytes(),method="POST",
        headers={"Authorization":"Bearer "+token(),"Accept":"application/vnd.github+json","Content-Type":"application/octet-stream"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=60) as response:return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError("GitHub asset upload returned HTTP "+str(error.code)) from None
