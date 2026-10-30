# 合成 GIS 样例

本项目开发前置配置创建的微型人工合成数据，无真实用户数据。`phase11_points.geojson` 遵循 GeoJSON WGS84 经度/纬度顺序，含 3 个二维点、分组 a/a/b、数值 10/20/30；独立预期为 count=3、sum(value)=60。它用于开发与环境检查，不属于 T07 留出集，也不证明缓冲、投影或裁剪正确。

`./scripts/dev.sh prepare` 复制到忽略的 shared/uploads；同名文件内容不同则拒绝覆盖。原始文件不可由工具写入。
