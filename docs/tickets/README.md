# 实施切片与正式 GitHub 任务

这里是一项切片一份文件的范围参考；正式状态以 GitHub 为准。依赖与阶段的权威索引为[实施清单](../implementation-plan.md)，目标行为与编号见[规格](../specs/0001-qgis-workflow-agent-v1.md#acceptance-criteria)。

- [T00 环境基线](00-environment-baseline.md)
- [T01 审核后加载图层](01-reviewed-layer-load.md)
- [T02 审核与恢复](02-review-recovery.md)
- [T03 验证 Processing 链](03-verified-processing-chain.md)
- [T04 可选 LangSmith](04-optional-langsmith.md)
- [T05 裁剪、融合、统计](05-clip-dissolve-statistics.md)
- [T06 审阅 Skill](06-reviewed-skills.md)
- [T07 真实评测](07-benchmark.md)
- [T08 交付](08-delivery.md)

正式任务链接如下；工作状态和原生阻塞在 GitHub 查询。编排规则见[任务交接](../agents/orchestration.md)。

## GitHub 正式索引

| 切片 | GitHub | 原生 Blocked by |
| --- | --- | --- |
| T00 | [#20](https://github.com/init-wei/qgis-workflow-agent/issues/20) | 无 |
| T01 | [#21](https://github.com/init-wei/qgis-workflow-agent/issues/21) | [T00 #20](https://github.com/init-wei/qgis-workflow-agent/issues/20) |
| T02 | [#22](https://github.com/init-wei/qgis-workflow-agent/issues/22) | [T01 #21](https://github.com/init-wei/qgis-workflow-agent/issues/21) |
| T03 | [#23](https://github.com/init-wei/qgis-workflow-agent/issues/23) | [T01 #21](https://github.com/init-wei/qgis-workflow-agent/issues/21) |
| T04 | [#24](https://github.com/init-wei/qgis-workflow-agent/issues/24) | [T03 #23](https://github.com/init-wei/qgis-workflow-agent/issues/23) |
| T05 | [#25](https://github.com/init-wei/qgis-workflow-agent/issues/25) | [T03 #23](https://github.com/init-wei/qgis-workflow-agent/issues/23) |
| T06 | [#26](https://github.com/init-wei/qgis-workflow-agent/issues/26) | [T05 #25](https://github.com/init-wei/qgis-workflow-agent/issues/25) |
| T07 | [#27](https://github.com/init-wei/qgis-workflow-agent/issues/27) | [T02 #22](https://github.com/init-wei/qgis-workflow-agent/issues/22)、[T06 #26](https://github.com/init-wei/qgis-workflow-agent/issues/26) |
| T08 | [#28](https://github.com/init-wei/qgis-workflow-agent/issues/28) | [T07 #27](https://github.com/init-wei/qgis-workflow-agent/issues/27) |
