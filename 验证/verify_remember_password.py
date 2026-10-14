"""Native Mac credential vault acceptance in a disposable keychain; no real account access."""
import json
import os
import secrets
import re
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent

def main():
    if sys.platform != 'darwin': raise SystemExit('Native Keychain acceptance requires macOS')
    with tempfile.TemporaryDirectory(prefix='polyu-remember-native-') as temporary:
        root=Path(temporary); keychain=root/'synthetic.keychain-db'; binary=root/'RememberPasswordChecks'
        before=subprocess.check_output(['security','list-keychains','-d','user'])
        password=secrets.token_urlsafe(32)
        subprocess.run(['xcrun','swiftc','-parse-as-library','-O','-module-cache-path',str(root/'modules'),
                        str(ROOT/'MacApp/CredentialVault.swift'),str(ROOT/'验证/RememberPasswordChecks.swift'),'-o',str(binary)],check=True)
        subprocess.run(['codesign','--force','--sign','-',str(binary)],check=True,capture_output=True)
        subprocess.run(['security','create-keychain','-p',password,str(keychain)],check=True,capture_output=True)
        try:
            env=os.environ.copy();env['POLYU_SYNTHETIC_KEYCHAIN_PASSWORD']=password
            result=subprocess.run([str(binary),str(root/'synthetic-app'),str(keychain)],env=env,check=False,capture_output=True,text=True,timeout=60)
            if result.returncode:
                marker=re.findall(r'SYNTHETIC_PHASE_[A-Z_]+|SYNTHETIC_CHECK_\d+|SYNTHETIC_KEYCHAIN_[A-Z_]+=[-0-9]+|CredentialFailure\.unavailable', result.stderr)
                raise RuntimeError('Synthetic keychain check failed: '+ ','.join(marker[:4]))
            line=result.stdout.strip()
            assert line.startswith('REMEMBER_CHECKS=')
            checks=int(line.split('=')[1]);assert checks>=25
            for mode in ('seed', 'reopen', 'forget'):
                process=subprocess.run([str(binary),str(root/'synthetic-app'),str(keychain),mode],env=env,check=True,capture_output=True,text=True,timeout=30)
                assert process.stdout.strip() == 'SYNTHETIC_PROCESS_OK'
        finally:
            subprocess.run(['security','delete-keychain',str(keychain)],check=True,capture_output=True)
            assert before==subprocess.check_output(['security','list-keychains','-d','user'])
    report={'ok':True,'checks':checks,'macosCredentialKeychainRoundTrip':True,'nativeProcessReopen':True,'writeFailurePreservesOriginal':True,
            'consentAndOwnerIsolation':True,'logoutPolicyRecovery':True,'realAccountAccessed':False,'realCanvasValidated':False}
    sys.path.insert(0, str(ROOT))
    from release_build import source_hashes
    report['sourceHashes'] = source_hashes()
    (ROOT/'验证/remember-keychain-evidence.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'sourceHashes'}))
if __name__=='__main__':main()
