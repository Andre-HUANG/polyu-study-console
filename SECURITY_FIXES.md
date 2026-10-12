# 1.8.1 安全修复

| 文件 | 修改与目的 |
| --- | --- |
| 学习控制台/server.py、local_access.py | 短时一次性原生 bootstrap；敏感读取/普通修改会话认证；Host/Origin/重复头检查；拒绝 chunked 修改；公开页无秘密；退出旋转会话/CSRF |
| 学习控制台/app.js | 兑换票据并立即删除 fragment；秘密不在公开脚本；保持原界面和 CSRF |
| MacApp/App.swift | 每次后台启动新原生秘密；禁止授权请求代理/重定向；非持久登录窗口；首页/重载强制新文档；退出清除 Keychain 与旧 WebKit，失败保留标记 |
| MacApp/SessionVault.swift、学习控制台/session_store.py、native_canvas.py | Keychain 秘密经 stdin/stdout；文件仅标记；迁移写入读回复核；失败不降级明文；保留 Windows DPAPI |
| WindowsApp/desktop.py | 后台启动旋转秘密、WebView2 bootstrap；专用目录、Job Object、私密模式及停止后清理保留 |
| .gitignore | 指定 JSON、实际目录、截图、OCR、日志、缓存、密钥、证书、构建中间物；取消跟踪生成清单；Mac ZIP 排除开发者扩展属性/资源旁文件 |
| security/privacy_scan.py、.gitleaks.toml、tools-lock.json | 当前/暂存/历史/完整包扫描，输出位置/指纹；分类哈希、许可证、测试及字体误报 |
| .github/workflows/ci.yml、release.yml | PR/main 安全、Python、两平台构建测试及发布门禁；默认只读权限；发布写权限仅可信 main；Action 固定 Commit |
| WindowsApp/assemble_windows.py、build-tools-lock.json | 原生 NSIS/平台依赖哈希固定；ASCII 命名；完整 x64 负载/DLL 验证 |
| WindowsApp/gui_smoke.py、installer_verify.py、验证/verify_local_api.py、verify_keychain.py | 真实回环 HTTP、临时 Keychain、原生 WebView2 与一次性 CI 安装卸载 |
| security/check.py、validate_platform.py、release.py | 统一失败即停止入口，四产物/原生证据/版本/Commit/哈希校验，拒绝重复 Tag/Release |

保留空框架、账号内缓存、成绩证据和独立手动记录。未删除真实数据或重写历史。v1.8.0 源码和原安装包保留；旧版不能直接理解 Keychain 标记，回退时保留学习目录并重新授权同一账号，不能导出明文 Cookie 回滚。旧版仍有已确认 API 风险，不建议长期使用。


额外确认并修复：旧 Mac 初始化会清理无法识别的档案，现改为原样保留；共享登录拒绝把无所有者/身份冲突的残留文件关联给新账号。未经账号授权不显示残留公告，显式退出确认后才能删除这些资料。

发布验收绑定干净的已提交源码、原生测试负载哈希和可信 main CI；拒绝旧证据、重复/缺失安装包及哈希变化。新增 11 项发布门禁回归。
