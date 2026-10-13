; Installs only the closed binary payload and current-user shortcuts/registration.
Unicode true
RequestExecutionLevel user
!include "MUI2.nsh"
!include "x64.nsh"
!include "LogicLib.nsh"
Name "PolyU study控制台 1.8.2"
OutFile "${INSTALLER_OUT}"
InstallDir "$LOCALAPPDATA\Programs\PolyUStudyConsole"
Icon "${APP_ICON}"
UninstallIcon "${APP_ICON}"
SetCompressor /SOLID lzma
ManifestDPIAware true
ManifestLongPathAware true
VIProductVersion "1.8.2.0"
VIAddVersionKey "ProductName" "PolyU study控制台"
VIAddVersionKey "ProductVersion" "1.8.2"
VIAddVersionKey "FileVersion" "1.8.2"
VIAddVersionKey "FileDescription" "PolyU study控制台 Windows x64 安装程序"
VIAddVersionKey "LegalCopyright" "Personal study tool; not an official university application"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "将为当前用户安装 PolyU study控制台 1.8.2。$\r$\n$\r$\n应用包含自己的 Python，无需 Codex、Python 或管理员权限。需要系统已有 .NET Framework 4.8 及 Microsoft Edge WebView2 Runtime。$\r$\n$\r$\n请对照 GitHub Release 的 SHA256 校验文件和验证说明。安装程序不安装系统组件；账号资料由应用管理。"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\PolyUStudyConsole.exe"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Function .onInit
  SetShellVarContext current
  SetRegView 64
  ${IfNot} ${IsNativeAMD64}
    MessageBox MB_ICONSTOP "此安装包适用于 AMD / Intel x64 Windows。"
    Abort
  ${EndIf}
  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\Windows NT\CurrentVersion" "CurrentBuildNumber"
  ${If} $0 < 19041
    MessageBox MB_ICONSTOP "需要 Windows 10 2004（build 19041）或更新版本。"
    Abort
  ${EndIf}
  ; No directory chooser or alternate account-profile target is accepted.
  StrCpy $INSTDIR "$LOCALAPPDATA\Programs\PolyUStudyConsole"
FunctionEnd

Section "应用"
  SetShellVarContext current
  SetRegView 64
  !include "${PAYLOAD_INSTALL}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  SetOutPath "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\PolyU study控制台"
  CreateShortcut "$SMPROGRAMS\PolyU study控制台\PolyU study控制台.lnk" "$INSTDIR\runtime\pythonw.exe" '-I -B -X utf8 "$INSTDIR\WindowsApp\main.py"' "$INSTDIR\console\assets\polyu-favicon.ico"
  CreateShortcut "$SMPROGRAMS\PolyU study控制台\安装与使用.lnk" "$INSTDIR\安装与使用.txt"
  CreateShortcut "$SMPROGRAMS\PolyU study控制台\卸载.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PolyUStudyConsole" "DisplayName" "PolyU study控制台"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PolyUStudyConsole" "DisplayVersion" "1.8.2"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PolyUStudyConsole" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PolyUStudyConsole" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PolyUStudyConsole" "DisplayIcon" "$INSTDIR\console\assets\polyu-favicon.ico"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PolyUStudyConsole" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PolyUStudyConsole" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  SetRegView 64
  ; Delete only package-owned files. Never recursively remove an account or folder.
  !include "${PAYLOAD_UNINSTALL}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$SMPROGRAMS\PolyU study控制台\PolyU study控制台.lnk"
  Delete "$SMPROGRAMS\PolyU study控制台\安装与使用.lnk"
  Delete "$SMPROGRAMS\PolyU study控制台\卸载.lnk"
  RMDir "$SMPROGRAMS\PolyU study控制台"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PolyUStudyConsole"
SectionEnd
