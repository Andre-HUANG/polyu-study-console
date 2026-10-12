# 安全与发布规则

适用版本为 1.8.1 源码修复；正式安装包状态见 [Releases](https://github.com/Andre-HUANG/polyu-study-console/releases)。详见 [审计](SECURITY_AUDIT.md)、[修复](SECURITY_FIXES.md)、[验证](SECURITY_TESTS.md)。

发现漏洞/凭据时使用仓库可用的私密安全报告渠道，不在公开 Issue/日志/截图/PR 放 Cookie、Token、账号、真实课程/成绩或证件。真实泄露先撤销凭据；报告保留类型、脱敏位置及指纹。

核对 remote 与工作区，提交采用 noreply。git config --local core.hooksPath security 启用钩子。统一入口 python3 security/release.py --check；暂存区另执行 python3 security/privacy_scan.py --staged。证据/调试资料不进入 Git，许可证原样保留。

CI 默认 contents read，不使用 pull_request_target，不给外部 PR 发布秘密。Action 固定 Commit，运行时/文档/Windows 依赖/NSIS/Gitleaks 检查来源与哈希。输入验证失败不可执行/发布。

发布只接受可信 main 同一 Commit 的成功跨平台 CI，验证完整清单、白名单、源码字节、架构、真实运行、安装卸载和 SHA256。发布步骤单独 contents write；失败停止，不强推、不改历史、不覆盖版本。默认 Draft，原生验收不足则先推进已测试源码文档。

Mac ad-hoc/未公证，Windows 未签名；真实 SSO 和签名变化升级仍需单独验收。本地认证/Keychain/DPAPI 不承诺同用户恶意代码或管理员完全隔离。历史私人邮箱须额外授权重写，本任务未清理公共副本。
