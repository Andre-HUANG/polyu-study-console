"""The pure-Python repack must be identical across host ZIP defaults."""
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import assemble_windows as assembly

class ArchiveRepackChecks(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        source=self.root/"source.tar.gz"
        with tarfile.open(source,"w:gz") as archive:
            for name,content in {"proxy_tools-0.1.0/proxy_tools/__init__.py":b"synthetic_module=True",
                "proxy_tools-0.1.0/PKG-INFO":b"Name: proxy-tools",
                "proxy_tools-0.1.0/README.rst":b"synthetic license"}.items():
                info=tarfile.TarInfo(name);info.size=len(content)
                archive.addfile(info,io.BytesIO(content))
        (self.root/"proxy_tools-upstream-LICENSE.txt").write_bytes(b"synthetic-public-license")
        self.lock={"sdists":[{"name":"proxy-tools","filename":source.name,"sha256":hashlib.sha256(source.read_bytes()).hexdigest()}],"builtWheels":[]}
        self.scope=patch.multiple(assembly,DOWNLOADS=self.root,SOURCE=self.root)
        self.scope.start()
    def tearDown(self):
        self.scope.stop();self.temp.cleanup()
    def test_different_host_zip_defaults_produce_the_same_locked_bytes(self):
        expected=assembly.pure_proxy_wheel(self.lock)
        baseline=(self.root/expected["filename"]).read_bytes()
        original=zipfile.ZipInfo
        class WindowsDefault(original):
            def __init__(self,*args,**kwargs):
                super().__init__(*args,**kwargs);self.create_system=0
        with patch.object(zipfile,"ZipInfo",WindowsDefault):
            actual=assembly.pure_proxy_wheel(self.lock)
        self.assertEqual(expected,actual)
        self.assertEqual(baseline,(self.root/expected["filename"]).read_bytes())
        with zipfile.ZipFile(self.root/expected["filename"]) as archive:
            self.assertTrue(all(i.create_system==3 and i.compress_type==zipfile.ZIP_STORED for i in archive.infolist()))
    def test_changed_expected_repack_hash_is_rejected(self):
        assembly.pure_proxy_wheel(self.lock)
        self.lock["builtWheels"][0]["sha256"]="0"*64
        with self.assertRaises(RuntimeError):assembly.pure_proxy_wheel(self.lock)

class RuntimePathChecks(unittest.TestCase):
    def test_only_fixed_system_clr_path_can_extend_private_paths(self):
        from native_verify import validated_paths
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle=root/"bundle";dotnet=root/"system-clr"
            self.assertTrue(validated_paths([str(bundle/"runtime"),str(dotnet)],bundle,dotnet))
            self.assertFalse(validated_paths([str(dotnet)],bundle))
    def test_other_external_and_empty_paths_are_rejected(self):
        from native_verify import validated_paths
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle=root/"bundle";dotnet=root/"system-clr"
            for extra in ("",str(root/"untrusted"),str(dotnet/"nested")):
                self.assertFalse(validated_paths([str(bundle/"runtime"),extra],bundle,dotnet))

class WindowsPayloadStructureChecks(unittest.TestCase):
    def test_portable_preserves_empty_loader_directories_and_unicode_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle=root/"bundle";bundle.mkdir()
            for arch in ("win-arm64","win-x64","win-x86"):
                (bundle/"runtime/Lib/site-packages/webview/lib/runtimes"/arch/"native").mkdir(parents=True)
            (bundle/"Windows原生自检.cmd").write_bytes(b"synthetic")
            archive_path=root/"portable.zip"
            assembly.deterministic_zip(bundle,archive_path)
            with zipfile.ZipFile(archive_path) as archive:archive.extractall(root/"extracted")
            extracted=root/"extracted/bundle"
            for arch in ("win-arm64","win-x64","win-x86"):
                self.assertTrue((extracted/"runtime/Lib/site-packages/webview/lib/runtimes"/arch/"native").is_dir())
            self.assertEqual((extracted/"Windows原生自检.cmd").read_bytes(),b"synthetic")

    def test_installer_keeps_empty_directory_and_explicit_utf8_include(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);bundle=root/"bundle";(bundle/"empty/native").mkdir(parents=True)
            (bundle/"Windows原生自检.cmd").write_bytes(b"synthetic")
            with patch.object(assembly,"CACHE",root):
                install,uninstall=assembly.installer_payload(bundle)
            for path in (install,uninstall):
                self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
                self.assertIn("Windows原生自检.cmd",path.read_text(encoding="utf-8-sig"))
            self.assertIn('CreateDirectory "$INSTDIR'+chr(92)+'empty'+chr(92)+'native"',install.read_text(encoding="utf-8-sig"))
            self.assertIn('RMDir "$INSTDIR'+chr(92)+'empty'+chr(92)+'native"',uninstall.read_text(encoding="utf-8-sig"))

    def test_compiler_uses_explicit_utf8_on_both_hosts(self):
        with tempfile.TemporaryDirectory() as temporary:
            for platform,prefix in (("win32","/"),("darwin","-")):
                with patch.object(assembly,"CACHE",Path(temporary)),patch.object(assembly.sys,"platform",platform),patch.object(assembly.subprocess,"run") as run:
                    assembly.compile_nsis(Path("synthetic-makensis"),Path("synthetic.nsi"),{},"synthetic-build.txt")
                command=run.call_args.args[0]
                self.assertEqual(command[command.index(prefix+"INPUTCHARSET")+1],"UTF8")


class WindowsVersionResourceChecks(unittest.TestCase):
    def resource(self, version, fixed_version=None):
        from types import SimpleNamespace
        parts = tuple(map(int, (fixed_version or version).split('.'))) + (0,)
        fixed = SimpleNamespace(FileVersionMS=(parts[0] << 16) | parts[1],
            FileVersionLS=(parts[2] << 16) | parts[3],
            ProductVersionMS=(parts[0] << 16) | parts[1], ProductVersionLS=(parts[2] << 16) | parts[3])
        table = SimpleNamespace(entries={b'ProductVersion': version.encode(), b'FileVersion': version.encode()})
        return SimpleNamespace(FileInfo=[[SimpleNamespace(StringTable=[table])]], VS_FIXEDFILEINFO=[fixed])

    def test_current_installer_and_launcher_version_resources_pass(self):
        self.assertTrue(assembly.verify_pe_version(self.resource(assembly.VERSION), assembly.VERSION))

    def test_stale_installer_version_is_rejected_even_with_current_payload(self):
        with self.assertRaisesRegex(RuntimeError, 'version resource'):
            assembly.verify_pe_version(self.resource('1.8.2'), assembly.VERSION)

    def test_stale_fixed_version_is_rejected_even_with_current_display_version(self):
        with self.assertRaisesRegex(RuntimeError, 'fixed version'):
            assembly.verify_pe_version(self.resource(assembly.VERSION, '1.8.2'), assembly.VERSION)

    def test_missing_and_conflicting_version_resources_are_rejected(self):
        pe = self.resource(assembly.VERSION)
        pe.VS_FIXEDFILEINFO = []
        with self.assertRaisesRegex(RuntimeError, 'Missing'):
            assembly.verify_pe_version(pe, assembly.VERSION)
        pe = self.resource(assembly.VERSION)
        pe.FileInfo += self.resource('1.8.2').FileInfo
        with self.assertRaisesRegex(RuntimeError, 'Conflicting'):
            assembly.verify_pe_version(pe, assembly.VERSION)
