"""Production Windows credential bridge on an intercepted synthetic AD FS document.
The SDK provides the source origin; no request reaches the school, and no real
credential, Canvas login or MFA is exercised.
"""
import time
from System import Action
from System.IO import MemoryStream
from System.Text import Encoding
from Microsoft.Web.WebView2.Core import CoreWebView2WebResourceContext

FIXTURE = """<!doctype html><meta charset=utf-8><title>Synthetic school form</title>
<form id=loginForm method=post action='https://adfs.polyu.edu.hk/adfs/ls/'>
<input id=userNameInput name=UserName type=email><input id=passwordInput name=Password type=password>
<button type=button onclick=\"document.getElementById('loginForm').submit()\">Synthetic manual submit</button></form>
<script>window.syntheticSubmits=0; document.getElementById('loginForm').submit=function(){window.syntheticSubmits++};</script>"""


def verify(app):
    username, password = 'synthetic-netid', 'synthetic-only-feature-smoke'
    app.credentials.enable()
    app.credentials.save_verified(username, password, '123')
    app.login()
    school = app.login_window
    def wait(condition):
        end = time.monotonic()+35
        while time.monotonic()<end:
            if condition(): return
            time.sleep(.1)
        raise RuntimeError('Synthetic WebView2 credential check timed out')
    def ui(operation):
        result = []
        def call(): result.append(operation())
        school.native.Invoke(Action(call))
        return result[0]
    app._credential_smoke_stage = 'core initialization'
    wait(lambda: app.credential_bridge is not None and ui(lambda: school.native.browser.webview.CoreWebView2 is not None))
    core = ui(lambda: school.native.browser.webview.CoreWebView2)
    def intercept(sender, event):
        if str(event.Request.Uri).startswith('https://adfs.polyu.edu.hk/'):
            payload = FIXTURE if '/adfs/ls/' in str(event.Request.Uri) else ''
            event.Response = core.Environment.CreateWebResourceResponse(MemoryStream(Encoding.UTF8.GetBytes(payload)), 200, 'OK', 'Content-Type: text/html; charset=utf-8\r\nCache-Control: no-store')
    def install():
        core.AddWebResourceRequestedFilter('https://adfs.polyu.edu.hk/*', CoreWebView2WebResourceContext.All)
        core.WebResourceRequested += intercept
    app._credential_smoke_stage = 'synthetic resource interception'
    school.native.Invoke(Action(install))
    school.load_url('https://adfs.polyu.edu.hk/adfs/ls/')
    app._credential_smoke_stage = 'native form fill'
    wait(lambda: app.login_attempt and app.login_attempt.filled)
    wait(lambda: school.evaluate_js("document.getElementById('userNameInput')?.value==='synthetic-netid' && document.getElementById('passwordInput')?.value==='synthetic-only-feature-smoke' && window.syntheticSubmits===0"))
    assert app.login_attempt.candidate is None
    app._credential_smoke_stage = 'manual candidate capture'
    school.run_js("document.getElementById('loginForm').submit()")
    wait(lambda: app.login_attempt and app.login_attempt.candidate is not None)
    assert app.login_attempt.candidate == (username,password)
    assert school.evaluate_js('window.syntheticSubmits===1')
    app._credential_smoke_stage = 'browser autosave settings'
    assert ui(lambda: not core.Settings.IsPasswordAutosaveEnabled and not core.Settings.IsGeneralAutofillEnabled)
    attempt = app.login_attempt
    app._credential_smoke_stage = 'cancel and forget'
    school.destroy()
    wait(lambda: app.login_window is None)
    assert not attempt.active and attempt.candidate is None
    app.credentials.forget()
    app.publish_credentials()
    return {'nativeCredentialFormFill':True,'nativeCredentialCandidateCapture':True,'manualSubmitOnly':True,
            'browserPasswordAutosaveDisabled':True,'schoolDocumentInterceptedSynthetic':True,'realCanvasValidated':False}
