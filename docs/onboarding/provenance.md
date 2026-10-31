# 源码与第三方来源

基线仓库为 [init-wei/qgis-workflow-agent](https://github.com/init-wei/qgis-workflow-agent)，固定起点 `9602db0dd517ab6f0ce0ea4514174425b9a55142`。本次克隆保留 Git 历史、既有署名、插件 metadata 和研究导出；继承仓库历史不能单独证明某个人编写或贡献了全部代码。

固定基线和当前 Git 根目录均未发现根级 `LICENSE`；不代作者补写许可证，也不据此推断可公开分发或第三方代码获得了新的开源授权。[QGIS 插件 metadata](../../qgis_mcp_server/qgis_mcp_plugin/metadata.txt) 的 `author=TuNombre` 与示例邮箱是继承自基线的占位内容，原样保留，不将其认定为真实作者或署名。发布时需依据实际来源另行核对。Python/npm 锁定依赖可能各有许可要求，本次没有完成全部依赖的许可证审计，也不对其重新授权。

V1 规格、架构、任务和规模测量于 2026-10-30 从同一用户会话的 RS Harness 临时研究目录迁入，本仓库成为后续规格和实施权威。合成 GIS 样例由本次配置创建，其[来源](../../tests/fixtures/gis/README.md)独立于用户真实数据。没有迁移 RS Harness 运行环境或模型权重。用户另行授权提供的服务端凭据与真实测试素材仅保存在忽略目录，处理与再分发边界见[测试数据说明](test-data.md)，不属于上述合成 fixture 或公开源码。
