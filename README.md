# PolyU study控制台

个人学习控制台，连接香港理工大学 Canvas，集中查看考试与作业、成绩与评分、课程变化和文档证据。学生制作的工具，非学校官方应用。

当前版本：**1.8.0**。首次启动为空框架，登录后读取当前账号的课程；没有默认课程、预设评分比例或随包账号资料。

## 下载与使用

从 [v1.8.0 Release](https://github.com/andre2582676978-dot/polyu-study-console/releases/tag/v1.8.0) 下载安装包，并对照 `SHA256SUMS.txt` 校验。

| 平台 | 文件 | 条件与验证范围 |
| --- | --- | --- |
| Apple Silicon Mac | `PolyU study控制台-1.8.0-arm64.dmg` 或 `.zip` | macOS 13 或更新版本；已完成隔离原生界面与生命周期检查。使用 ad-hoc 签名，未进行 Apple 公证。 |
| Windows x64 | `PolyU study控制台-1.8.0-windows-x64-setup.exe` 或 `.zip` | Windows 10 2004 或更新版本，需要 .NET Framework 4.8、WebView2 Runtime。已完成二进制组装和静态检查，尚未进行 Windows 原生运行验收；未签名。 |

安装后打开「设置 → Canvas 账号 → 登录并授权 Canvas」，在学校登录页面完成验证。应用打开期间默认每小时同步，也可手动更新。详情见 [使用说明](学习控制台/使用说明.md)、[Mac 安装说明](MacApp/安装与使用.txt) 和 [Windows 安装说明](WindowsApp/安装与使用.txt)。

## 功能

- 显示 Canvas 作业、公告、已公布成绩及最近确认的内容变化。
- 下载当前账号可见的课程模块及页面附件，再读取正文、PDF 表格与本机 OCR；保留文件名、页码、来源链接及原文。
- 将文件日期、Canvas 截止时间与个人安排核对；日期不明确、未公布或来源冲突时保留待核对状态。
- 在评分比例组成明确的 100% 方案且考核项匹配可靠时折算已公布成绩；冲突、未知和未评分项目保持空白。Canvas 当前成绩单独显示。
- 在本账号已读取的文档中搜索，并查看最近 200 条固定操作日志。

正式个人考试安排以学校 eStudent 信息为准。下载与识别界面只报告实际可见、可读取的范围。

## 数据存储

Canvas 采集使用只读请求。登录会话只用于学校 Canvas 主机；文件转到支持的存储域名时不转发 Cookie 或认证头。学习界面服务仅监听 `127.0.0.1`。

Mac 资料位于 `~/Library/Application Support/PolyU study控制台/account/`；Windows 位于 `%LOCALAPPDATA%\PolyU study控制台\account\`。退出登录会先停止后台任务，再删除全部账号资料、文档缓存、日志和登录会话。正常关闭应用保留本账号资料。

此仓库包含应用源码、公开素材、锁定依赖及合成测试，不包含实际账号、课程材料或登录凭据。

## 从源码构建

源码直接位于仓库根目录。`MacApp/` 是原生 Mac 外壳，`WindowsApp/` 是 Windows 外壳，`学习控制台/` 是共享界面与采集管线，`验证/` 是隔离测试工具。

Apple Silicon Mac 需要 Python 3.12 和 Xcode Command Line Tools。先恢复原始许可证与哈希锁定的文档依赖，再下载固定的公开 Python 运行时：

```sh
python3 MacApp/prepare_document_dependencies.py
python3 MacApp/download_runtime.py
python3 验证/run_offline.py
python3 MacApp/build.py
```

生成文件位于 `Mac发布/`。依赖归档为原项目锁定文件的压缩副本；恢复脚本校验归档、文件清单及每个文件的 SHA256。构建时检查并保留最终 arm64 二进制。

Windows 原生构建需要 Windows x64 与 Python 3.12，按 [原生构建与验收](WindowsApp/原生构建与验收.md) 执行。锁定清单位于 `WindowsApp/`；Windows 使用自己的依赖，不能使用 Mac 的原生依赖。

## 审查与来源

上传前代码审查与验证范围见 [REVIEW.md](REVIEW.md)。更新内容见 [CHANGELOG.md](CHANGELOG.md)。

学校标识来自官方素材，原始网址见 [SOURCES.txt](学习控制台/assets/SOURCES.txt)。第三方组件保留原始许可证及固定版本清单，见 [NOTICE.md](NOTICE.md)。本项目源码尚未指定开放源代码许可证。
