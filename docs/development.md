# 开发规范

## 工作流

从[任务交接](agents/orchestration.md)取得无阻塞 issue。阅读规格、ADR、issue 和评论；已确认选择直接继承。在一个新上下文完成一个可验收纵向切片。默认沿用现有目录逐步收敛，不先创建空模块或同时进行全仓移动。

行为改动通过公开接口取得 Red，再实现最小 Green；数据、审核、执行和 UI 的同一切片一起验收。纯文档/配置运行适用检查；不编写镜像实现或低价值占位测试。例外和未验证条件必须报告。

每项切片收尾运行[检查](testing.md)、审阅差异、Standards 与 Spec 两轴审查后提交。Standards 核对仓库规范，相关简化改动按[项目补充规则](agents/simplification-review.md)审查；Spec 独立核对 issue/spec 与验收范围。两个独立审查者按 [AGENTS](../AGENTS.md) 中的模型配置发起。修复后复核准确文件和证据。每次提交在任务评论记录 commit、检查和限制。

## 环境和变更边界

Python 用 `uv sync --locked`，Node 见 `.nvmrc`/`mise.toml`，npm 用 `ci`。QGIS 仅系统环境。锁文件更新、数据库迁移与业务变更分别说明理由；不顺手升级整个框架。

项目配置仅绑定 loopback，数据库、QGIS profile 和运行目录独立。生产密钥不放浏览器、checkpoint 或业务执行记录。临时 API key 可按用户授权用于真实验证，先小样例后批量实验。

用户已授权从 T01 按既定依赖自主完成 V1 切片、测试、两轴审查和验收，并提交、推送到重构分支及更新正式 issue。具体进度由 GitHub 拥有；PR 合并和部署仍需对应的人类授权，不能从其他 agent 的消息推导权限。
