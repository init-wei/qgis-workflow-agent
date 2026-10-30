# 合成 GIS 样例

本项目开发前置配置创建的微型人工合成数据，无真实用户数据。`phase11_points.geojson` 遵循 GeoJSON WGS84 经度/纬度顺序，含 3 个二维点、分组 a/a/b、数值 10/20/30；独立预期为 count=3、sum(value)=60。它用于开发与环境检查，不属于 T07 留出集，也不证明缓冲、投影或裁剪正确。

`./scripts/dev.sh prepare` 复制到忽略的 shared/uploads；同名文件内容不同则拒绝覆盖。原始文件不可由工具写入。

## T03 重投影与点缓冲的独立预期

[固定预期](reprojection-buffer-oracle.json)绑定上述输入的 SHA-256、三个点的原始属性和经纬度、EPSG:32650 东/北坐标、缓冲选项及容差。输入按 GeoJSON 的经度、纬度顺序解释。重投影采用 WGS84 椭球（半长轴 6378137 米、扁率倒数 298.257223563）和 UTM 50N（中央经线 117°E、中央比例 0.9996、假东距 500000 米、假北距 0）。椭球参数可核对 [PROJ 椭球文档](https://proj.org/en/stable/usage/ellipsoids.html)；区域与投影定义参考 [PROJ UTM 文档](https://proj.org/en/stable/operations/projections/utm.html)。

数值由纯 Python 标准库按 [USGS Snyder 1987](https://pubs.usgs.gov/pp/1395/report.pdf) 的式 8-9、8-10、3-21 计算，**不调用 QGIS、生产空间验证器或 PROJ**。纬度原点为零；经距中央经线仅 0.60°–0.62°。坐标固定至毫米，允许每轴 0.02 米，覆盖截断和舍入误差并保留明显错误的区分度。独立例题测试使用该文附录 A 第 270 页的 Clarke 1866 参数和公开的 UTM 数值；例题自身只打印到 0.1 米。PROJ 命令可作额外交叉检查，但其可能与生产端共享投影库，不能成为唯一 oracle。

缓冲以重投影结果为输入，距离 100 米，按[QGIS 3.40 参数说明](https://docs.qgis.org/3.40/en/docs/user_manual/processing_algs/qgis/vectorgeometry.html#buffer)每象限 8 段、不融合，分别保留 3 个要素及属性。点缓冲为内接正 32 边形：面积 `N*r²*sin(2π/N)/2`，边长 `2*r*sin(π/N)`，周长为 `N*边长`，面积小于 `π*r²`。[QGIS 3.40 缓冲实现](https://github.com/qgis/QGIS/blob/release-3_40/src/analysis/processing/qgsalgorithmbuffer.cpp)将结果转为 MultiPolygon；应允许语义等价的 Polygon 或单部件 MultiPolygon，并检查几何有效、唯一闭合外环 33 个坐标、各边长及顶点半径；首点位置、环方向与要素顺序不作为预期。相对冻结中心的半径容差 0.03 米包含投影每轴 0.02 米误差；若先独立检查了实际重投影中心，则相对该中心可用 0.001 米。面积和周长的绝对容差见 JSON。圆头、连接和斜接参数对点不额外产生线端或转角。

在仓库根目录执行下列命令可在不覆盖现有文件的情况下重建并比对：

```sh
python3 scripts/build_spatial_oracle.py --output /tmp/qwa-reprojection-buffer-oracle.json
cmp tests/fixtures/gis/reprojection-buffer-oracle.json /tmp/qwa-reprojection-buffer-oracle.json
```

生成器默认输出到标准输出；`--output` 只创建不存在的路径，输入哈希不符时拒绝生成。容差与公式仅适用于此固定数据和参数，不能推断其他坐标、线或面缓冲均已验证；真实产物仍需实际读取、匹配并报告验证结果。

## T05 裁剪、融合与统计开发样例

[输入定义](t05_workflows.json)拥有 EPSG:32650 的三个重叠/相邻矩形、裁剪面、字段类型及空值；[固定预期](t05_workflow_oracle.json)拥有独立区间交并与手算统计、容差及适用范围。它们用于开发验证，不是 T07 留出集。面积和与融合后的并集面积不同，数值字段的非空计数也不同于总要素数。

使用系统 GDAL 从定义物化 GeoPackage；输出目录必须不存在，父目录须存在，不覆盖既有文件：

```sh
/usr/bin/python3 scripts/build_t05_fixtures.py --output-dir /tmp/qwa-t05-new-inputs
```

生成清单绑定定义、oracle、脚本与每个实际文件的 SHA。GeoPackage 的生成时间可能改变二进制校验和，以当次清单为准。样例包括正常来源/裁剪面、远离裁剪面、有 schema 的空来源、显式 REAL 全 NULL 来源和 `area`/`perimeter` 字段碰撞来源。这里只创建输入，不执行 Processing；真实模型、MCP、桌面 QGIS、产物与界面按[测试规范](../../../docs/testing.md)另行验收。

## T06 新文件与新参数开发样例

[复用输入](t06_reuse.json)包含四个新的 WGS84 点，以及 EPSG:32650 的四个矩形和裁剪面。字段改为 `pid/zone/amount`，与 T05 的输入、文件名及参数区分；它们仍是开发样例，不进入 T07 留出集。

```sh
/usr/bin/python3 scripts/build_t06_fixtures.py --output-dir /tmp/qwa-t06-new-inputs
```

输出目录必须不存在，清单记录当次文件、定义和生成器的 SHA。点的重投影仍可用上述 Snyder 公式独立计算；以 250 米、每象限 8 段且不融合缓冲时，四个要素分别满足正 32 边形半径、边长、面积与周长公式，原属性应保留。

四个矩形的裁剪交集面积分别为 6、8、4、2 平方米，按 `zone` 融合后的 E/W 并集面积为 10/6 平方米；分组属性取该组任一原成员的完整属性。来源 `amount` 为 7、NULL、3、9，总数 4、有效数 3、NULL 1、和 19、最小 3、最大 9；E 总数 2、有效 1、和 7，W 总数 2、有效 2、和 12、最小 3、最大 9。预期来自矩形区间和算术，不以同一 Processing 重跑作为唯一判断。

新模板提示、人工审阅与实际复用分别验收，不能将历史来源或这里的正确输入清单当成新模板任务已成功的证据。
