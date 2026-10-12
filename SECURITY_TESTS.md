# 1.8.1 验证记录

PASS=本项实际执行通过（注明合成/静态/原生范围）；FAIL=执行失败；NOT TESTED=尚未执行；BLOCKED=环境/权限不支持。合成通过不等于真实 Canvas 账号验收。

## 已执行

- PASS：152 项隔离回归，0 外部 socket/DNS 尝试。覆盖空框架、课程/作业/Quiz、考试分类、日期歧义、评分比例/冲突、文档/检索、下载/重试/重定向、日志、手动记录、迁移及退出清理。
- PASS：20 个实际 loopback HTTP 检查，无授权读取/修改拒绝、正确读写、错误 Host/Origin/Cookie、票据单次、退出失效、模拟重启失效、合成账号 A→B 隔离。
- PASS：独立临时 Keychain 实际写入、读取、旧 Cookie 迁移、相同签名 helper 重装、锁定失败保护、清理及既有搜索列表恢复；未访问真实应用账号。
- PASS：当前源码、全部可达历史及锁定依赖扫描。私人邮箱保留为 ACKNOWLEDGED_HISTORY，并未清除；Gitleaks 上游规则加精确哈希误报规则。
- PASS：Mac ARM64 构建、ZIP CRC、DMG 校验、深度严格 ad-hoc 签名、白名单及独立运行时/PDFKit/PDF 表格/Vision 空图检查。

## 原功能与平台矩阵

| 项目 | 状态/范围 |
| --- | --- |
| Canvas 登录、SSO/MFA、真实状态恢复 | NOT TESTED；未连接真实账号 |
| Cookie 域限制、只读 URL、重定向去认证头 | PASS，合成传输回归 |
| 退出/切换、手动/自动同步、部分失败继续扫描 | PASS，合成回归与真实 loopback |
| 课程、作业、Quiz、公告、已公布成绩 | PASS，合成数据；真实当前账号 NOT TESTED |
| 日期提取/冲突、评分比例/折算 | PASS，合成证据回归 |
| PDF/表格、Word、OCR 空图、全文检索 | PASS，解析回归与 Mac PDFKit/Vision；Windows 原生见 CI |
| 课程变化、固定日志 | PASS，回归；真实课程变化 NOT TESTED |
| Mac 启动/关闭/重开/后台故障恢复/退出清理 | PASS：本次 3 阶段真实原生 GUI 验收，含关闭重开、后台故障恢复和退出取消/确认；使用隔离合成账号 |
| Mac 包内容/架构/签名 | PASS，构建与结构；未 Apple 公证 |
| Windows DPAPI 真正系统往返 | 本地 Mac BLOCKED；Windows CI --platform-check 后决定 |
| Windows WebView2 启动/关闭/本地授权/设置 | 本地 Mac BLOCKED；Windows CI --gui-smoke 后决定 |
| Windows 原生安装/卸载/保留账号目录 | 本地 Mac BLOCKED；一次性 Windows CI 后决定 |
| 新 Windows x64 PE/DLL/锁定负载 | PASS：Mac 组装 1.8.1 x64 候选包并完成 PE/DLL/哈希检查；不代替 Windows 运行 |
| 不同 ad-hoc 签名身份跨版本 Keychain 访问 | NOT TESTED，可能要求系统授权；失败保留数据 |
| 真实既有用户升级/异常退出/实际卸载 | 合成迁移/故障回归 PASS；真实用户环境 NOT TESTED |

## 可复用流程

python3 security/release.py --check 执行扫描、Gitleaks、回归、HTTP 和可用的 Keychain 检查。--build mac/windows 在原生平台构建验收。证据只在忽略目录，公开仅脱敏摘要和 CI 明确白名单。

[远程 Actions](https://github.com/Andre-HUANG/polyu-study-console/actions)：Security Scan、Python Tests、macOS Build and Test、Windows Build and Test、Release Validation 均需成功。发布只接受同一 main SHA 的成功运行，失败停止；未执行项保持未验证。

CI 的 Mac/Windows 开发解释器固定为 GitHub 官方清单提供的 Python 3.12.10；随应用的哈希锁定运行时仍为 3.12.15，另有实际包运行验收。PR 检出并验证其 head Commit，发布只接受成功 main 运行。GitHub 自动 PR 预览的私人邮箱元数据见审计剩余风险。
