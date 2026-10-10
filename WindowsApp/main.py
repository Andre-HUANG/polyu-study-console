"""Application entry for the private runtime and native PyInstaller builds."""
import json
import sys
from pathlib import Path

BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
CONSOLE = BASE / "console"
if not CONSOLE.is_dir():
    CONSOLE = BASE / "学习控制台"
sys.path.insert(0, str(CONSOLE))
if not getattr(sys, "frozen", False):
    # -I intentionally omits the script directory; explicitly admit only this
    # application's own shell modules in both source and private-runtime layouts.
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    if sys.argv[1:] == ["--backend"]:
        import server
        sys.argv = ["server.py", "--no-open"]
        server.main()
    elif sys.argv[1:2] == ["--pdf-layout"]:
        if len(sys.argv) != 4: raise SystemExit("PDF helper arguments invalid")
        from pdf_layout import read_layout
        try:
            selected = json.loads(sys.argv[3])
            if not isinstance(selected, list) or any(type(n) is not int or n < 1 for n in selected):
                raise ValueError("Invalid PDF page list")
            print(json.dumps(read_layout(sys.argv[2], selected), ensure_ascii=False))
        except Exception as error:
            print(json.dumps({"error": "PDF 表格解析器：" + type(error).__name__}, ensure_ascii=False))
            raise SystemExit(1)
    elif sys.argv[1:] == ["--platform-check"]:
        from native_verify import main as verify
        verify()
    elif sys.argv[1:]:
        raise SystemExit("Unsupported application mode")
    else:
        from desktop import main as desktop_main
        desktop_main()


if __name__ == "__main__":
    try:
        main()
    except (Exception, SystemExit) as error:
        if isinstance(error, SystemExit) and error.code in (None, 0):
            raise
        if sys.platform == "win32" and not sys.argv[1:]:
            import ctypes
            message = (str(error) + "\n\n需要 Windows x64、.NET Framework 4.8 或更新版本，以及 Microsoft Edge WebView2 Runtime。"
                       "\n如缺少运行环境，请使用安装与使用说明中的微软官方链接。本应用不会自动安装系统组件。")
            ctypes.windll.user32.MessageBoxW(None, message, "PolyU study控制台 · 1.8.0", 0x10)
            raise SystemExit(1)
        else:
            raise
