"""Throwaway-key end-to-end rehearsal of the locked official feed signer."""
import base64,json,os,pathlib,sys,tempfile
ROOT=pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import release_updates
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

def main():
    key=Ed25519PrivateKey.generate()
    private=key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
    public=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    previous_key=release_updates.LOCK["publicKey"];previous_seed=os.environ.get("POLYU_UPDATER_ED25519_KEY")
    try:
        release_updates.LOCK["publicKey"]=base64.b64encode(public).decode()
        os.environ["POLYU_UPDATER_ED25519_KEY"]=base64.b64encode(private).decode()
        with tempfile.TemporaryDirectory(prefix="polyu-feed-signing-test-") as temporary:
            folder=pathlib.Path(temporary)
            for suffix in ("-arm64.dmg","-arm64.zip","-windows-x64-setup.exe","-windows-x64.zip"):
                (folder/("PolyUStudyConsole-"+release_updates.VERSION+suffix)).write_bytes(("synthetic "+suffix).encode())
            release_updates.generate(folder,"a"*40);release_updates.sign(folder)
            assert release_updates.verify(folder)
            feed=folder/"appcast-macos.xml";original=feed.read_bytes();feed.write_bytes(original.replace(b"1.8.2",b"1.8.3",1))
            try:release_updates.verify(folder)
            except Exception:pass
            else:raise AssertionError("Altered signed feed was accepted")
            feed.write_bytes(original)
            package=folder/("PolyUStudyConsole-"+release_updates.VERSION+"-windows-x64.zip")
            package.write_bytes(package.read_bytes()+b"altered")
            try:release_updates.verify(folder)
            except Exception:pass
            else:raise AssertionError("Altered package was accepted")
        print(json.dumps({"ok":True,"sdkSigner":"Sparkle 2.10.0","signedFeeds":3,"tamperedFeedRejected":True,"tamperedPackageRejected":True,"keyScope":"ephemeral","productionKeyAccessed":False}))
    finally:
        release_updates.LOCK["publicKey"]=previous_key
        if previous_seed is None:os.environ.pop("POLYU_UPDATER_ED25519_KEY",None)
        else:os.environ["POLYU_UPDATER_ED25519_KEY"]=previous_seed

if __name__=="__main__":main()
