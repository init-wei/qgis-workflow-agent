# 六类模板审阅清单

本清单对应六份初始 `1.0.0` 模板。当前状态全部为 `agent_curated / pending_human_review`，尚未记录人类审阅；原任务成功、代理代码审查和浏览器自动点击均不替代这项事实。模板只提供规划参考，每次实际调用仍需完整计划审核；这里的例值不授权未来任务。

| 模板 | 策划内容与需核对的边界 | 真实来源 |
| --- | --- | --- |
| [qwa-load](../../agent/skills/load.json) | 读取一个用户选定的 2D 矢量图层并加载；使用当前 file_id、受控路径与图层名，不产生新空间文件 | [T01 加载验收](https://github.com/init-wei/qgis-workflow-agent/issues/21#issuecomment-6526916539)，task `3b9ea257-e98c-4a15-b23e-e3b450cba382` |
| [qwa-reproject](../../agent/skills/reproject.json) | 显式目标 CRS 与新 GPKG；`EPSG:32650` 仅为示例，用户未明确目标不能沿用 | [T03 组合验收](https://github.com/init-wei/qgis-workflow-agent/issues/23#issuecomment-6529726256)，task `f35b427f-6ef7-490f-83ad-f2fa8349bd5a` 的重投影步骤 |
| [qwa-buffer](../../agent/skills/buffer.json) | 先重投影再按米缓冲；示例 100 米、每象限 8 段、不融合。实际距离、单位和明确选项由当前需求约束，属性与几何另验 | 同一 T03 任务的重投影与缓冲步骤；不能据此保证任意投影或几何的解释质量 |
| [qwa-clip](../../agent/skills/clip.json) | 两个当前文件分别作来源与裁剪面，输出新 GPKG；参考角色顺序不代替用户意图，CRS 与真实交集另验 | [T05 验收](https://github.com/init-wei/qgis-workflow-agent/issues/25#issuecomment-6532123611)，task `8a7b119c-d342-45ad-aba9-6b587e86d274` 的裁剪步骤 |
| [qwa-dissolve](../../agent/skills/dissolve.json) | 按明确字段融合，例值 `grp` 与 `SEPARATE_DISJOINT=false`；不能替用户选择分组字段，非分组属性来自原成员 | 同一 T05 任务的按字段融合步骤；全部融合仍需明确模式及实际产物验证 |
| [qwa-statistics](../../agent/skills/statistics.json) | 对明确数值字段输出 GPKG 统计表；例值 `value`，总要素数、非空数、NULL、sum/min/max 分别表达 | 同一 T05 验收中的 task `a1b8ae64-8f22-4992-85a8-a6d8d02b803c` 数值统计步骤 |

输入条件、别名、变量类型、完整 TaskPlan 示例与来源范围以对应 JSON 为准。方案目录只使用轻量标签、别名及输入条件匹配，不声称理解任意自然语言或保证检索精度；候选列表不能证明模型实际采用了模板。新数据与参数复验属于开发验收，正式泛化比较按 T07 冻结任务集执行。

审阅应核对：六类范围与原任务证据一致；例值仅作参考；缺参数或不能可靠对应时要求明确；新文件通过受控 ID 与路径绑定；数字、布尔值和列表不被字符串化；模板不能跳过计划、风险、产物验证与结果确认；没有自动注册或 Cookbook 第二方案源。

维护者确认具体版本后，记录真实 `human_reviewed` 事实及其证据。下列规范化内容 SHA 仅绑定被审阅的版本和内容，不是签名，也不是已发生审阅的证明：

| ID | 版本 | 内容 SHA-256 |
| --- | --- | --- |
| qwa-load | 1.0.0 | `e37b4f2c948f5b4b4734cd069b830833f76191e6580916e8c26df7bd532cef8d` |
| qwa-reproject | 1.0.0 | `f01e75e9bd6714aff707cafd1cd3847cf82d1b4168023f7bbf47fb5d152b9ad5` |
| qwa-buffer | 1.0.0 | `74ed1d7d3af6ca41108c020621aace5e44414870e2cead4798514e2715ac121f` |
| qwa-clip | 1.0.0 | `0e2ce1c821c4ae1ac92775fba52d6050178865b4bb7906a3a988dd862e7fe622` |
| qwa-dissolve | 1.0.0 | `a936a3b67a7b28f0fa95a2511fe37331594752d99281e537b0d6d0fc2689c6fb` |
| qwa-statistics | 1.0.0 | `739ca499b7021cc4d4beea4c0a34da2caa87a402906868e7154d3cf8a1147bb4` |

计算方法与审阅记录结构见[目录说明](../../agent/skills/README.md)。实际实施与未完成验收由[正式 T06](https://github.com/init-wei/qgis-workflow-agent/issues/26)拥有；本清单不自行宣称该票完成。
