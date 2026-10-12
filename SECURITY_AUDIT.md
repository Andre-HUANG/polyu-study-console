# 安全审计｜2026-10-12｜1.8.1

独立检查指定公共仓库 Andre-HUANG/polyu-study-console，起点 d176d8e791fb0a79c9ad8d8b00af488bdb8538f7。已核对全部可见远程分支、Tag、本地分支及可达对象；远程起点仅 main 与 v1.8.0，本地另有 publication-preparation。原工作区干净，修复在独立 worktree/分支进行。未重写历史、强推、删除旧 Release 或读取/清除真实应用账号。

## 已确认发现

| 风险 | 证据与范围 | 处理 |
| --- | --- | --- |
| P0 私人提交邮箱及作者身份 | 6 个公共历史提交 Author 含私人 Gmail，部分 Committer 也含私人 Gmail；不展示完整地址 | 未来改为 ID+用户名 noreply；旧历史保留，等待额外授权 |
| P0 本地未授权读取 | 旧 /api/data、/api/documents/search 只校验 loopback Host，任意本地 HTTP 客户端可读取当前学习资料 | 原生秘密认证的短时票据、HttpOnly 会话和敏感读取控制 |
| P0 公开 CSRF 被误用作认证 | 旧首页向未授权客户端返回 CSRF，普通修改仅依赖它与可伪造 Origin | 未授权页面不含 CSRF；读取与修改独立验证客户端会话 |
| P0 生成文件上传缺口 | 指定 GUI/包证据 JSON 未被完整忽略，还生成未覆盖的 验证/native-gui/；publication-checks 已跟踪 | 忽略全部生成证据/日志/截图/中间目录；取消跟踪生成清单 |
| P1 Mac 长期明文 Cookie | 旧 save_private 仅 Windows 使用 DPAPI，Mac 保存 Cookie 数组 | Keychain helper；迁移写入及读回复核，失败保留原文件 |
| P1 旧仓库链接 | remote、README、v1.8.0 Release 审查链接使用旧用户名 | 更新当前 remote/文档；旧 Release 链接由仓库发布工具修正 |

受影响公共 Commit：b381ab2889a0、fb9799c58f3f、0f002f34e6fb、9cb23a9073de、8de6eb473613、d176d8e791fb。本地 1226b974d2c1 使用 noreply。

没有确认已公开的真实 Canvas Cookie、GitHub Token、OpenAI Key 或私钥。“未发现”不证明所有可能凭据均不存在。若确认真实凭据泄露，须撤销/轮换对应会话、Token、Key 或私钥；删除字符串或改历史不能代替撤销。

## 误报与范围限制

学校官网 URL 的 /department/home/ 最初被路径正则误报，复核后排除；未据此确认本机用户名/路径泄露。保留官方素材及学校/Adobe 元数据，未发现项目拥有者身份。依赖的公开作者邮箱、构建路径、许可证、SSH PEM 格式常量及 Pillow 内嵌字体中的偶然令牌前缀与私人凭据区分。Gitleaks 把清单 12 处 SHA256 误报为 generic-api-key；仅在明确列出的依赖/构建清单路径允许严格 64 位十六进制哈希。

源码没有随包账号、课程资料或真实成绩目录；合成测试改用测试标识。未知姓名、地址、课程笔记、复杂 EXIF/XMP 及未公开/不可达对象不能仅靠正则证明无泄露。仓库范围限本连接可访问对象。

## 1.8.0 安装包独立审计

现存可信构建文件 SHA256 与 GitHub Release API digest 完全一致，未沿用 REVIEW.md 的结论。

| 文件 | SHA256 | 本次结果 |
| --- | --- | --- |
| Mac ARM64 DMG | 0c4e61a7a63ca105c796c52f56388e73b2b5100b478bb44cd3202a0516d7ec2e | PASS：只读挂载、完整目录无账号路径，3 个主要二进制 arm64，深度严格签名通过 |
| Mac ARM64 ZIP | 6a1817f3d9e255e4897f0404418fbf1920e716605fcf87e008d0461a6fbd2ee4 | 4,640 项；CRC 通过；无账号/凭据缓存路径；拥有者邮箱/本机用户名命中 0 |
| Windows Setup | 770a392500104764bf8273dd7a8df860d07ae4eb42b3abe0f6b0a8b7bf487a8b | 官方哈希锁定 7-Zip 解出 2,136 文件；2,131 个负载与 Portable 清单逐一一致，无缺失/变更 |
| Windows Portable ZIP | 1699a638b92dbbf91ade458986c75c63af4c6d29e6e41c906de2826d048a0c2f | 2,132 项；CRC 通过；无账号/凭据缓存路径；拥有者邮箱/用户名命中 0 |
| document-vendor.zip | a02ac8b749df11924e0e054ab933f68c614261d6d29a749e673b2bc60a45b955 | 620 个锁定文件逐一验证；许可证保留；常量/内嵌字体误报已分类 |

旧包扫描未发现开发者个人文件；CPython、Pillow、cryptography 的第三方作者和上游构建路径仍存在，不判为拥有者私人信息。Windows 真实 SSO、原生安装运行及所有未知二进制调试段不因静态检查变成 PASS。DMG 本次已独立只读挂载检查，未启动旧应用或读取真实账号。

## 剩余风险

HTTP 随机凭据降低无授权本地调用和跨站攻击；Host、Origin、CSRF、CSP、只读 Canvas 和 127.0.0.1 保留。HttpOnly Cookie 按本进程/端口命名，但 Cookie 不提供端口级操作系统进程隔离。控制台限制导航，公开资源无秘密；不能抵御同用户恶意代码读取进程、劫持客户端、访问 Keychain 或 DPAPI。文档/成绩未做整目录加密。

Mac 新登录窗口改为非持久 WebKit，只有 Keychain 中的 Canvas 会话长期保存。旧版 WebKit 持久副本可能在升级后仍存在，直到用户明确退出登录才清理；本任务不自动清除真实用户会话。

Mac 仍 ad-hoc 签名且未公证。Keychain 指定代码要求可随 ad-hoc 版本变化；相同 helper 重装通过，变更签名身份跨版本尚未实机验证，可能要求系统授权。失败保持原账号与保护文件，禁止无提示明文降级。真实 Keychain/SSO 升级不能用合成测试替代。参考 [Apple 代码签名要求](https://developer.apple.com/documentation/technotes/tn3127-inside-code-signing-requirements)、[Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)。

## 历史邮箱清理方案（尚未执行）

1. 获额外授权后冻结提交，建立私密可恢复镜像；记录 branch、Tag、Release 目标与签名。
2. 独立镜像用 git-filter-repo mailmap 精确替换受影响 Author/Committer 的姓名/邮箱，保留公开 handle/noreply，复核全部 refs。
3. 评估后代 Commit SHA 变化、annotated Tag 重新签名、PR/Issue/文档/Release 引用更新及 CI 失效。二进制校验和不因仅提交元数据重写变化，但来源关系须重新说明。
4. 另行授权并协调协作者后才推送重写 refs；现有克隆需要重克隆或严格清理，不能把旧历史重新合入。旧 fork、缓存、下载副本及索引无法保证清除，必要时联系 GitHub Support。
5. 本任务不执行历史重写、Tag 删除或强推。

可在 GitHub 邮箱设置启用隐藏邮箱和阻止私人邮箱命令行推送。仓库使用本账号 ID 的 noreply 以关联账号；全局 Git 配置未擅自改动。[GitHub 邮箱说明](https://docs.github.com/en/account-and-profile/concepts/email-addresses)


额外确认并修复：旧 Mac 初始化会清理无法识别的档案，现改为原样保留；共享登录拒绝把无所有者/身份冲突的残留文件关联给新账号。未经账号授权不显示残留公告，显式退出确认后才能删除这些资料。

## GitHub PR 预览提交的新发现

本次 PR #1 的 GitHub 自动合并预览 Commit 7bede98586f4 的 Author 仍含私人 Gmail（Committer 为 GitHub）。它不是本地 noreply 创建的正式源提交，未进入 main；本次不删除或重写它。后续预览对象也可能由相同账号设置产生，不能宣称这些公开对象已清除。

已提醒用户在 GitHub 邮箱设置启用隐藏邮箱和阻止私人邮箱命令行推送；本地仓库配置无法替代账号设置。CI 明确检出并扫描实际 PR head、全部正式分支及 Tag，最终更新 main 使用通过 CI 的原始 noreply 提交和正常 fast-forward。预览元数据单独报告，不作为新正式源码提交；后续新的正式私人邮箱提交仍会被扫描拒绝。

用户已于本次执行中确认启用 GitHub 的隐藏邮箱和阻止私人邮箱推送设置；此前已生成的预览对象仍按残留风险处理，后续对象需重新核查。

设置生效已独立验证：后续自动预览 24bdcf50702f 的 Author 使用 users.noreply.github.com。此前 7bede98586f4 / cf77278aa81b 的私人邮箱对象仍未清除。
