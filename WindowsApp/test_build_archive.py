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
