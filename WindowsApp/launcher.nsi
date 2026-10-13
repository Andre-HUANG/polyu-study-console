; A native NSIS GUI bootstrap. The Python application payload is AMD64.
Unicode true
RequestExecutionLevel user
Name "PolyU study控制台"
OutFile "${LAUNCHER_OUT}"
Icon "${APP_ICON}"
SilentInstall silent
AutoCloseWindow true
ShowInstDetails nevershow
ManifestDPIAware true
ManifestLongPathAware true
VIProductVersion "${RELEASE_VERSION}.0"
VIAddVersionKey "ProductName" "PolyU study控制台"
VIAddVersionKey "ProductVersion" "${RELEASE_VERSION}"
VIAddVersionKey "FileVersion" "${RELEASE_VERSION}"
VIAddVersionKey "FileDescription" "PolyU study控制台启动程序"
VIAddVersionKey "LegalCopyright" "Personal study tool; not an official university application"
Section
  SetOutPath "$EXEDIR"
  IfFileExists "$EXEDIR\runtime\pythonw.exe" ready
    MessageBox MB_ICONSTOP "应用目录不完整，请重新解压完整 ZIP 或重新安装。"
    SetErrorLevel 1
    Quit
  ready:
  ClearErrors
  Exec '"$EXEDIR\runtime\pythonw.exe" -I -B -X utf8 "$EXEDIR\WindowsApp\main.py"'
  IfErrors failed done
  failed:
    MessageBox MB_ICONSTOP "无法启动应用。请核对完整安装目录和安装与使用说明中的系统运行条件。"
    SetErrorLevel 1
  done:
SectionEnd
