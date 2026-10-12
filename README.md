# PolyU Study Console｜PolyU study控制台

连接香港理工大学 Canvas，集中查看考试与作业、成绩、课程变化和文档证据。学生制作的工具，非学校官方应用。首次启动为空框架，没有默认课程、预设评分比例或随包账号资料。

源码版本：**1.8.1**。最新正式安装包以 [GitHub Releases](https://github.com/Andre-HUANG/polyu-study-console/releases) 的实际状态为准；1.8.1 在两平台原生验收与发布门禁通过前保持候选状态。旧版 [v1.8.0](https://github.com/Andre-HUANG/polyu-study-console/releases/tag/v1.8.0) 保留，含本次确认的本地 API 访问风险。

## 安装

| 平台 | 安装包 | 系统与签名 |
| --- | --- | --- |
| Apple Silicon Mac | PolyUStudyConsole-1.8.1-arm64.dmg、.zip | macOS 13+，arm64；ad-hoc 签名，**未 Apple 公证** |
| AMD / Intel Windows | PolyUStudyConsole-1.8.1-windows-x64-setup.exe、.zip | Windows 10 2004+、.NET Framework 4.8、WebView2 Runtime；未 Authenticode 签名 |

下载后对照同一 Release 的 SHA256SUMS.txt 校验。Mac 将完整 app 复制到应用程序目录；Windows 使用 Setup 或完整解压 Portable ZIP。应用自带 Python。安装说明见 [Mac](MacApp/安装与使用.txt) 与 [Windows](WindowsApp/安装与使用.txt)。只有正式 Release 中出现的文件才是已发布下载；CI 候选包不代表完成真实 Canvas SSO 验证。

打开「设置 → Canvas 账号 → 登录并授权 Canvas」，在学校网页完成验证。默认每小时同步，可手动更新。正式个人考试安排以学校 eStudent 信息为准。

## 隐私与数据

- Canvas 请求保持只读。Cookie 仅发送到 canvas.polyu.edu.hk；批准的 HTTPS 文件存储重定向不转发 Cookie、Authorization 或 Referer。
- 后端仅监听 127.0.0.1。静态页面公开；课程、成绩、文档搜索和日志读取需原生客户端建立的随机会话。一次性启动凭据 30 秒失效，使用后立即清除，退出和重启撤销旧会话。修改请求同时检查 Origin、CSRF 和客户端会话，原生操作另有独立秘密。
- Mac 新登录窗口使用非持久 WebKit，Canvas Cookie 保存到系统 Keychain。旧版持久 WebKit 副本在用户明确退出登录时清理。账号目录的 Cookie 文件仅留保护标记；旧明文在 Keychain 写入并独立读回复核成功后原子替换。失败保留原资料并显示错误，禁止无提示回退明文。ad-hoc 签名 helper 更新可能要求重新允许钥匙串访问；不同签名身份跨版本升级尚未实机验收。
- Windows Cookie 保留当前用户 DPAPI 加密。WebView2 使用专用私密会话目录；退出后关闭所有窗口和写入进程，再清除资料。
- HTTP 凭据、Keychain 与 DPAPI **不能完全隔离同一操作系统用户权限下的恶意程序**；管理员、调试权限、系统备份及磁盘取证也超出本次防护边界。文档、成绩及手动记录依赖账户目录权限，未宣称整目录加密。
- 没有新增遥测、日志上传或学习资料上传。

Mac 数据：~/Library/Application Support/PolyU study控制台/account/。Windows 数据：%LOCALAPPDATA%\PolyU study控制台\account\。正常关闭保留资料。「退出登录并删除」先停止后台与解析进程，再删除账号、课程、成绩、手动记录、缓存、日志和登录会话。Windows 卸载保留独立账号目录；要删除资料请先在应用中退出登录。迁移失败时不要手动删文件，先检查钥匙串授权或重新授权同一账号。

历史中确认的私人提交邮箱尚未清除；未来提交已采用 GitHub noreply。历史清理需额外授权，影响与恢复方案见 [审计报告](SECURITY_AUDIT.md)。

## 功能与验证

当前账号可见课程、作业、Quiz、公告、已公布成绩及附件；PDF 表格、Word 正文、本机 OCR 与全文检索；来源、页码、原文、未公布日期和冲突状态均保留。评分方案不明确或匹配不可靠时保持空白。Canvas 当前成绩单独显示。

[SECURITY_TESTS.md](SECURITY_TESTS.md) 区分合成回归、真实本机运行、未执行及环境阻塞。真实 Canvas SSO、当前真实课表与成绩、不同 ad-hoc 签名 helper 更新未被离线测试验证。[Actions](https://github.com/Andre-HUANG/polyu-study-console/actions) 是远程 CI 实际结果的来源。

## 开发与发布

Mac 需要 Apple Silicon、Python 3.12、Xcode Command Line Tools：

~~~sh
python3 MacApp/prepare_document_dependencies.py
python3 MacApp/download_runtime.py
python3 security/release.py --check
python3 security/release.py --build mac
~~~

Windows x64 使用 Python 3.12：

~~~sh
python WindowsApp/assemble_windows.py --download
python security/release.py --build windows
~~~

Windows 输入、NSIS、运行时及依赖逐一校验锁定哈希，保留原许可证；Mac 文档依赖归档逐文件复核。Mac 上可组装 Windows 二进制，但只能报告静态结果，不能作为 Windows 原生验收。

启用提交钩子：

~~~sh
git config --local core.hooksPath security
python3 security/privacy_scan.py --staged
~~~

统一流程：本地检查 → Commit → Push/PR → 安全与两平台 CI → 合并 main → 验证同一 main Commit 的产物 → Release。发布工作流只接受成功的可信 main 验证运行，检查四个安装文件、原生证据、SHA256、版本和 Commit；默认 Draft，显式选择发布后才正式发布。不覆盖已有 Tag/Release。详见 [SECURITY.md](SECURITY.md)。

学校素材保持官方原始比例与颜色，来源见 [SOURCES.txt](学习控制台/assets/SOURCES.txt)。依赖说明见 [NOTICE.md](NOTICE.md)。源码尚未指定开放源代码许可证。
