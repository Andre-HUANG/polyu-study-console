# PolyU Study Console｜PolyU study控制台

连接香港理工大学 Canvas，集中查看考试与作业、成绩、课程变化和文档证据。学生制作的工具，非学校官方应用。首次启动为空框架，没有默认课程、预设评分比例或随包账号资料。

当前开发版本：**2.3.0（构建 230，候选）**，以 2.2.0 / 构建 220 为基线，新增日期、时间、地点及完整评分方案的核实与应用流程。已处理事项移入可撤销的核实记录，未公布日期单列。详见 [2.3 更新说明](release/v2.3.0.md) 与 [验收范围](release/validation-2.3.0.md)。安装包与签名更新源以 [GitHub Releases](https://github.com/Andre-HUANG/polyu-study-console/releases/latest) 的实际文件为准；本仓库候选源码仍需通过下述发布门禁。所有历史 Release、标签和既有下载资产继续保留。

## 可下载版本

| 版本 | 功能与适用范围 | 下载与对应说明 |
| --- | --- | --- |
| v2.2.0 | 作业完成标记与撤销、当前筛选导出 .ics 日历、未来 14 天负荷条、成绩目标测算、侧栏滚动高亮 | [安装包、SHA256 与发行说明](https://github.com/Andre-HUANG/polyu-study-console/releases/tag/v2.2.0) · [验收范围](release/validation-2.2.0.md) |
| v2.1.0 | 更新界面、日期紧迫程度和成绩进度条；手动安排支持改名与调整课程 | [安装包、SHA256 与发行说明](https://github.com/Andre-HUANG/polyu-study-console/releases/tag/v2.1.0) · [验收范围](release/validation-2.1.0.md) |
| v2.0.0 | 学习总览与快捷资料搜索、安排搜索和日期范围、过期核对视图、校历个人安排、交互与安装器修复 | [安装包、SHA256 与发行说明](https://github.com/Andre-HUANG/polyu-study-console/releases/tag/v2.0.0) · [验收范围](release/validation-2.0.0.md) |
| v1.8.1 | 历史隐私安全修复版：本地 API 认证、Mac Keychain、Windows DPAPI 与跨平台打包修复；保留原始发布文件及哈希 | [安装包、SHA256 与发行说明](https://github.com/Andre-HUANG/polyu-study-console/releases/tag/v1.8.1) · [该版本源码](https://github.com/Andre-HUANG/polyu-study-console/tree/v1.8.1) |
| v1.9.1（1.9 系列） | 历史版本：包含 1.8.2 的课程表、性能优化和签名更新，以及默认关闭、用户主动开启的本机记住密码 | [安装包、SHA256 与发行说明](https://github.com/Andre-HUANG/polyu-study-console/releases/tag/v1.9.1) · [该版本源码](https://github.com/Andre-HUANG/polyu-study-console/tree/v1.9.1) |

每个版本均提供 Mac ARM64 DMG/ZIP、Windows x64 Setup/Portable ZIP。请使用同一版本的 SHA256SUMS.txt 校验。签名状态、系统要求、真实学校登录的验证边界分别见对应 Release。

## 安装

2.3 的目标安装包命名如下；只有正式 Release 中实际提供的文件才是已发布下载。

| 平台 | 安装包 | 系统与签名 |
| --- | --- | --- |
| Apple Silicon Mac | PolyUStudyConsole-2.3.0-arm64.dmg、.zip | macOS 13+，arm64；ad-hoc 签名，**未 Apple 公证** |
| AMD / Intel Windows | PolyUStudyConsole-2.3.0-windows-x64-setup.exe、.zip | Windows 10 2004+、.NET Framework 4.8、WebView2 Runtime；未 Authenticode 签名 |

下载后对照同一 Release 的 SHA256SUMS.txt 校验。Mac 将完整 app 复制到应用程序目录；Windows 使用 Setup 或完整解压 Portable ZIP。应用自带 Python。安装说明见 [Mac](MacApp/安装与使用.txt) 与 [Windows](WindowsApp/安装与使用.txt)。只有正式 Release 中出现的文件才是已发布下载；CI 候选包不代表完成真实 Canvas SSO 验证。

打开「设置 → Canvas 账号 → 登录并授权 Canvas」，在学校网页完成验证。可主动开启「在本机记住账号和密码」，同意说明后，本次或下次成功登录才会保存；以后打开学校登录窗口自动填入一次，仍需手动点击登录和完成学校验证。默认每小时同步，可手动更新。正式个人考试安排以学校 eStudent 信息为准。

## 2.3 新增

- 首页、考试与作业区以及评分明细提供核实入口。查看原文后，确认日期、时间、地点或完整评分方案，结果直接用于相关安排、校历或符合条件的成绩折算。
- 已核实事项不再占据待核实列表；「核实记录」默认折叠，展示确认结果和来源，可撤销。来源发生变化时，旧决定停止应用并重新提示核实。
- 未公布日期单独列出，后续同步继续查找。已获知日期可补充；不适用的日期事项需填写原因后排除，可随时撤销。
- 完整评分方案必须合计 100%；复杂规则、隐藏总成绩及未解决的 Canvas 权重冲突不能靠人工确认绕过，未知或未公布成绩继续留空。
- 核实决定仅保存在本机当前账号的 `documents/reviews.json`，不改写 Canvas 或原文件，退出登录随账号删除。首页补齐课表核对入口，课程概况的下一项跳过已完成事项。

## 2.2 已有功能

- 作业行可「标记完成」：已完成事项默认隐藏，「显示已完成」可查看或撤销。首页数字、最近安排、过期提醒和校历圆点只计未完成事项。标记只保存在本机账号目录，绑定截止时间；截止日期变化后自动失效并重新提醒。Canvas 明确显示未提交的作业需要再次确认，Canvas 的提交状态不会因此改变。
- 「导出日历」把当前筛选中已确认日期的考试与作业写成 `account/exports/polyu-schedule.ics`（香港时间；有具体时间的提前 24 小时提醒，全天事项前一天 9:00 提醒），并在访达或资源管理器中定位，可导入 Apple 日历、Outlook 或 Google 日历。日期待核对和已过日期的事项不导出；文件不上传，退出登录时随账号删除。
- 「接下来的安排」显示未来 14 天负荷条，点按日期在校历中查看当天清单。
- 成绩卡片只在评分方案完整核对（合计 100%、无权重冲突或复杂规则）且已有折算时提供「目标测算」：输入目标总评，估算尚未计入部分需要的平均得分率；未公布成绩不按零分计。
- 侧栏随页面滚动高亮当前栏目；以上新控件均不增加后台轮询。

## 2.0 日常使用

- 首页按未连接、会话失效、同步中和部分失败显示当前状态与下一步入口。
- 顶栏「搜索资料」或 Cmd/Ctrl K 打开已有资料全文检索；结果保留来源、页码和原文。
- 考试与作业支持按名称、课程、地点与备注搜索，并筛选未来 7 天、30 天或查看已过日期。文档和手动安排的日期已过仅表示需要核对；只有 Canvas 明确返回未提交时才显示未提交。
- 校历展示个人安排标记和选中日期的清单；学校考试期不能代替个人考试时间。
- 弹窗内直接显示错误，资料加载失败可重试；键盘导航、焦点、字号和窄窗口布局一并改进。隐藏窗口时继续暂停前端请求。

## 隐私与数据

- Canvas 请求保持只读。Cookie 仅发送到 canvas.polyu.edu.hk；批准的 HTTPS 文件存储重定向不转发 Cookie、Authorization 或 Referer。
- 后端仅监听 127.0.0.1。静态页面公开；课程、成绩、文档搜索和日志读取需原生客户端建立的随机会话。一次性启动凭据 30 秒失效，使用后立即清除，退出和重启撤销旧会话。修改请求同时检查 Origin、CSRF 和客户端会话，原生操作另有独立秘密。
- Mac 新登录窗口使用非持久 WebKit，Canvas Cookie 保存到系统 Keychain。旧版持久 WebKit 副本在用户明确退出登录时清理。账号目录的 Cookie 文件仅留保护标记；旧明文在 Keychain 写入并独立读回复核成功后原子替换。失败保留原资料并显示错误，禁止无提示回退明文。ad-hoc 签名 helper 更新可能要求重新允许钥匙串访问；不同签名身份跨版本升级尚未实机验收。
- Windows Cookie 保留当前用户 DPAPI 加密。WebView2 使用专用私密会话目录；退出后关闭所有窗口和写入进程，再清除资料。
- HTTP 凭据、Keychain 与 DPAPI **不能完全隔离同一操作系统用户权限下的恶意程序**；管理员、调试权限、系统备份及磁盘取证也超出本次防护边界。文档、成绩及手动记录依赖账户目录权限，未宣称整目录加密。
- 记住密码默认关闭；用户名和密码由原生窗口直接管理，密码不经过本地 HTTP API。macOS 使用独立、不参与 iCloud 同步的钥匙串记录；Windows 使用当前用户 DPAPI。写入后复核，失败不回退明文。账号区域仅显示保存状态和遮蔽用户名。
- 「删除已保存密码并关闭」不删除当前学习资料或会话；删除失败会阻止自动填入并显示未完成。主窗口正常关闭及学校会话到期保留已保存密码。学校会话有效期由学校控制。
- 没有新增遥测、日志上传或学习资料上传。

Mac 数据：~/Library/Application Support/PolyU study控制台/account/。Windows 数据：%LOCALAPPDATA%\PolyU study控制台\account\。正常关闭保留资料。「退出登录并删除」先停止后台与解析进程，再删除账号、课程、成绩、手动记录、缓存、日志和登录会话；已记住的密码默认一并删除。用户可在每次退出确认中主动勾选「在本机保留已记住的账号密码」，仅保留独立 credentials/ 目录和系统保护记录，不保留学习资料或 Cookie。退出选择写入恢复标记，中断后继续执行该选择。Windows 卸载保留独立账号目录；要删除资料请先在应用中退出登录。迁移失败时不要手动删文件，先检查钥匙串授权或重新授权同一账号。

历史中确认的私人提交邮箱尚未清除；未来提交已采用 GitHub noreply。历史清理需额外授权，影响与恢复方案见 [审计报告](SECURITY_AUDIT.md)。

## 功能与验证

当前账号可见课程、作业、Quiz、公告、已公布成绩及附件；PDF 表格、Word 正文、本机 OCR 与全文检索；来源、页码、原文、未公布日期和冲突状态均保留。评分方案不明确或匹配不可靠时保持空白。Canvas 当前成绩单独显示。

[SECURITY_TESTS.md](SECURITY_TESTS.md) 区分合成回归、真实本机运行、未执行及环境阻塞。真实学校登录/MFA、个人课表准确性、不同 ad-hoc 签名 helper 更新均不由离线测试证明；本次已完成检查和待完成门禁见 [2.3 验收说明](release/validation-2.3.0.md)。[Actions](https://github.com/Andre-HUANG/polyu-study-console/actions) 是远程 CI 实际结果的来源。

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

统一流程：本地检查 → Commit → Push/PR → 安全与两平台 CI → 合并 main → 验证同一 main Commit 的产物 → Release。发布工作流只接受成功的可信 main 验证运行，检查四个安装文件、原生证据、SHA256、版本和 Commit；默认 Draft，显式选择发布后才正式发布。不覆盖已有 Tag/Release。最终提交还须取得 24 小时内的签名真实服务与性能收据，沿用现有更新公钥。发布后核对远程 Tag/Commit、下载 SHA256 与历史资产保留情况。详见 [SECURITY.md](SECURITY.md)。

学校素材保持官方原始比例与颜色，来源见 [SOURCES.txt](学习控制台/assets/SOURCES.txt)。依赖说明见 [NOTICE.md](NOTICE.md)。源码尚未指定开放源代码许可证。
