#!/usr/bin/env python3
"""
根据类名或方法名称提取 QGIS API 文档内容
支持:
  1. 提取单个方法: QgsRasterBlock.noDataValue
  2. 提取整个类: QgsSingleBandPseudoColorRenderer
自动尝试不同的 API 分类 (core, gui, processing, server, _3d, analysis)
"""

import json
import re
import requests
import uvicorn
import os
import logging
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any, List
import sys
from mcp.server.fastmcp import FastMCP, Context
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row


class QGISMethodExtractor:
    """QGIS API 方法内容提取器"""

    # 可能的 API 分类
    CATEGORIES = ['core', 'gui', 'processing', 'server', '_3d', 'analysis']

    # 基础 URL
    BASE_URL = "https://qgis.org/pyqgis/master"

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

    def parse_method_name(self, api_name: str) -> Optional[Dict[str, Any]]:
        """
        解析方法名称，提取类名和方法名

        Args:
            api_name: 如 "QgsRasterBlock.noDataValue" 或 "QgsRasterBlock"

        Returns:
            dict: 包含 class_name 和 method_name（如果是纯类名，method_name为None）
        """
        if '.' not in api_name:
            # 纯类名，没有方法
            return {
                'class_name': api_name,
                'method_name': None,
                'full_name': api_name
            }

        parts = api_name.split('.')
        class_name = parts[0]
        method_name_part = '.'.join(parts[1:]) if len(parts) > 1 else ''

        return {
            'class_name': class_name,
            'method_name': method_name_part,
            'full_name': api_name
        }

    def build_url(self, category: str, class_name: str) -> str:
        """构建 API 文档 URL"""
        return f"{self.BASE_URL}/{category}/{class_name}.html"

    def extract_method_content(self, url: str, class_name: str, method_name: str) -> Optional[Dict[str, Any]]:
        """
        从指定 URL 提取方法内容

        Args:
            url: API 文档 URL
            class_name: 类名
            method_name: 方法名

        Returns:
            提取的内容字典，如果失败返回 None
        """
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # 查找方法签名（通常在 dl/dd 结构中）
            method_content = self._extract_method_details(soup, class_name, method_name, url)

            if method_content:
                return method_content

            return None

        except Exception:
            return None

    def _extract_method_details(self, soup: BeautifulSoup, class_name: str, method_name: str, url: str) -> Optional[Dict[str, Any]]:
        """
        从 BeautifulSoup 对象中提取方法详情
        """
        # 尝试查找方法标题
        meta = soup.find('meta', property='og:title')
        if meta and meta.get('content'):
            prefix = meta['content'].split('.')[0]
            method_id = f"qgis.{prefix}.{class_name}.{method_name}"
        else:
            method_id = f"qgis.core.{class_name}.{method_name}"

        # 方法 1: 通过 ID 查找
        method_section = soup.find('dt', {'id': method_id})
        if not method_section:
            # 尝试更灵活的匹配
            for dt in soup.find_all('dt'):
                text = dt.get_text()
                if method_name in text and class_name in str(dt.get('id', '')):
                    method_section = dt
                    break

        if not method_section:
            return None

        # 获取下一个 dd 元素（内容）
        content_dd = method_section.find_next('dd')
        if not content_dd:
            return None

        # 提取签名和所有信息
        signature = self._extract_signature(method_section)
        description = self._extract_description(content_dd)
        parameters = self._extract_parameters(content_dd)
        returns = self._extract_returns(content_dd)
        examples = self._extract_examples(content_dd)

        # 构建完整的方法名
        full_method_name = f"{class_name}.{method_name}"

        # 构建 content 字段，包含所有信息
        content_parts = []

        if description:
            content_parts.append(description)

        if parameters:
            content_parts.append("\n\nParameters:")
            for param in parameters:
                param_info = f"  - {param['name']}"
                if param.get('type'):
                    param_info += f" ({param['type']})"
                if param.get('description'):
                    param_info += f": {param['description']}"
                content_parts.append(param_info)

        if returns and (returns.get('type') or returns.get('description')):
            content_parts.append("\n\nReturns:")
            if returns.get('type'):
                content_parts.append(f"  Type: {returns['type']}")
            if returns.get('description'):
                content_parts.append(f"  {returns['description']}")

        if examples:
            content_parts.append("\n\nExamples:")
            for i, example in enumerate(examples, 1):
                content_parts.append(f"  Example {i}:\n{example}")

        # 返回简化格式
        result = {
            'api_name': full_method_name,
            'signature': signature,
            'content': '\n'.join(content_parts) if content_parts else '',
            'source_url': url
        }

        return result

    def _extract_signature(self, dt_element) -> Optional[str]:
        """提取方法签名"""
        try:
            code = dt_element.find('code')
            if code:
                signature = code.get_text(strip=True)
            else:
                signature = dt_element.get_text(strip=True)
            
            # 移除末尾的 ¶ 或 [source]¶
            signature = signature.rstrip('¶').strip()
            signature = re.sub(r'\[source\]\s*$', '', signature).strip()
            
            return signature
        except Exception:
            return None

    def _extract_description(self, dd_element) -> Optional[str]:
        """提取方法/类描述（包括所有 p 标签的内容）"""
        try:
            # 收集所有直接子级的 p 标签
            description_parts = []
            
            # 获取所有直接在 dd 下的 p 标签
            for p in dd_element.find_all('p', recursive=False):
                text = p.get_text(strip=True)
                if text:
                    # 移除 "New in version" 等注释
                    text = re.sub(r'\s*\(New in version.*?\)\s*', ' ', text)
                    text = text.strip()
                    if text:
                        description_parts.append(text)
            
            # 查找嵌套的属性定义（对于嵌套类）
            nested_attributes = []
            for nested_dl in dd_element.find_all('dl', class_='py attribute', recursive=False):
                for dt in nested_dl.find_all('dt'):
                    attr_name = dt.get_text(strip=True)
                    # 移除末尾的 ¶ 符号
                    attr_name = attr_name.rstrip('¶').strip()
                    if attr_name:
                        nested_attributes.append(f"  • {attr_name}")
            
            # 如果有嵌套属性，添加到描述中
            if nested_attributes:
                description_parts.append("\nAttributes:")
                description_parts.extend(nested_attributes)
            
            if description_parts:
                return '\n'.join(description_parts)
                
        except Exception:
            pass
        return None

    def _extract_parameters(self, dd_element) -> list:
        """提取参数列表"""
        parameters = []

        try:
            # 查找参数列表
            field_list = dd_element.find('dl', class_='field-list')
            if not field_list:
                field_list = dd_element.find('dl')

            if field_list:
                # 查找 Parameters dt 元素
                for dt in field_list.find_all('dt'):
                    text = dt.get_text(strip=True).lower()
                    if 'parameter' in text:
                        # 找到对应的 dd
                        param_dd = dt.find_next('dd')
                        if param_dd:
                            # 情况1: 查找 ul 列表（多个参数）
                            ul = param_dd.find('ul')
                            if ul:
                                # 遍历每个 li
                                for li in ul.find_all('li'):
                                    p = li.find('p')
                                    if p:
                                        # 提取参数名和类型
                                        strong = p.find('strong')
                                        em = p.find('em')
                                        
                                        param_name = strong.get_text(strip=True) if strong else ''
                                        param_type = em.get_text(strip=True) if em else ''
                                        
                                        parameters.append({
                                            'name': param_name,
                                            'type': param_type,
                                            'description': ''
                                        })
                            else:
                                # 情况2: 单个参数，直接在 p 标签中
                                p = param_dd.find('p')
                                if p:
                                    strong = p.find('strong')
                                    if strong:
                                        param_name = strong.get_text(strip=True)
                                        # 提取类型（在括号中）
                                        full_text = p.get_text(strip=True)
                                        # 移除参数名
                                        type_text = full_text.replace(param_name, '').strip()
                                        # 提取括号中的内容作为类型
                                        type_match = re.search(r'\(([^)]+)\)', type_text)
                                        param_type = type_match.group(1) if type_match else type_text.strip('()')
                                        
                                        parameters.append({
                                            'name': param_name,
                                            'type': param_type,
                                            'description': ''
                                        })
        except Exception:
            pass

        return parameters

    def _extract_returns(self, dd_element) -> Optional[Dict[str, str]]:
        """提取返回值信息"""
        try:
            field_list = dd_element.find('dl', class_='field-list')
            if not field_list:
                field_list = dd_element.find('dl')

            if field_list:
                # 查找 returns 字段
                for dt in field_list.find_all('dt'):
                    text = dt.get_text(strip=True).lower()
                    if 'return' in text or 'returns' in text:
                        dd = dt.find_next('dd')
                        if dd:
                            return_type = dd.find('code')
                            return_type_text = return_type.get_text(strip=True) if return_type else ''
                            return_desc = dd.get_text(strip=True)

                            if return_type_text:
                                return_desc = return_desc.replace(return_type_text, '').strip()

                            return {
                                'type': return_type_text,
                                'description': return_desc
                            }
        except Exception:
            pass
        return None

    def _extract_examples(self, dd_element) -> list:
        """提取代码示例"""
        examples = []

        try:
            # 查找所有代码块
            code_blocks = dd_element.find_all(['pre', 'div'], class_='highlight')
            for block in code_blocks:
                code_text = block.get_text(strip=True)
                if code_text:
                    examples.append(code_text)
        except Exception:
            pass

        return examples

    def _extract_base_classes(self, soup: BeautifulSoup) -> list:
        """提取基类信息"""
        base_classes = []
        try:
            # 查找 "Base classes" 部分
            for heading in soup.find_all(['h2', 'h3']):
                if 'Base classes' in heading.get_text():
                    # 找到对应的表格或列表
                    table = heading.find_next('table')
                    if table:
                        for row in table.find_all('tr'):
                            cells = row.find_all('td')
                            if cells:
                                class_link = cells[0].find('a')
                                if class_link:
                                    base_classes.append(class_link.get_text(strip=True))
                    break
        except Exception:
            pass
        return base_classes
    def _extract_methods_descriptions(self, soup: BeautifulSoup) -> Dict[str, str]:
        """提取页面开头表格中的方法描述"""
        method_descriptions = {}
        try:
            # 查找 "Methods" 和 "Static Methods" 部分的表格
            for heading in soup.find_all(['h2', 'h3', 'p']):
                heading_text = heading.get_text(strip=True)
                if heading_text in ['Methods', 'Static Methods']:
                    # 找到对应的表格（下一个兄弟元素）
                    table = heading.find_next('table')
                    if table:
                        for row in table.find_all('tr'):
                            cells = row.find_all('td')
                            if len(cells) >= 2:
                                # 第一列是方法名，第二列是描述
                                method_name = cells[0].get_text(strip=True)
                                description = cells[1].get_text(strip=True)
                                if method_name:
                                    method_descriptions[method_name] = description
        except Exception:
            pass
        return method_descriptions
    def _extract_methods_list(self, soup: BeautifulSoup, method_descriptions: Dict[str, str]) -> list:
        """提取方法列表（名称、签名和描述）"""
        methods = []
        try:
            # 查找所有方法定义的 dt 标签
            for dt in soup.find_all('dt', class_='sig sig-object py'):
                dt_id = dt.get('id', '')
                # 只提取方法，不提取类定义本身
                if dt_id and 'qgis.' in dt_id:
                    # 提取方法名（从 ID 中获取最后一部分）
                    parts = dt_id.split('.')
                    if len(parts) >= 3:  # qgis.core.ClassName.methodName
                        method_name = parts[-1]
                        # 提取签名
                        signature = self._extract_signature(dt)
                        if signature and method_name:
                            # 从描述字典中查找对应的描述
                            description = method_descriptions.get(method_name, '')
                            methods.append({
                                'name': method_name,
                                'signature': signature,
                                'description': description
                            })
        except Exception:
            pass
        return methods

    def _extract_full_class_content(self, soup: BeautifulSoup) -> str:
        """提取类的完整内容（从 Methods 开始的所有内容）"""
        content_parts = []
        try:
            # 查找主要内容区域
            main_content = soup.find('div', class_='body')
            if not main_content:
                main_content = soup.find('section')
            
            if main_content:
                # 提取类描述（第一个段落）
                first_p = main_content.find('p')
                if first_p:
                    content_parts.append(first_p.get_text(strip=True))
                
                # 提取所有方法的详细信息
                for dl in main_content.find_all('dl', class_='py method'):
                    for dt in dl.find_all('dt', recursive=False):
                        signature = self._extract_signature(dt)
                        if signature:
                            content_parts.append(f"\n{signature}")
                        
                        dd = dt.find_next('dd')
                        if dd:
                            # 提取方法描述
                            desc = self._extract_description(dd)
                            if desc:
                                content_parts.append(desc)
                            
                            # 提取参数
                            params = self._extract_parameters(dd)
                            if params:
                                content_parts.append("\nParameters:")
                                for param in params:
                                    param_info = f"  - {param['name']}"
                                    if param.get('type'):
                                        param_info += f" ({param['type']})"
                                    if param.get('description'):
                                        param_info += f": {param['description']}"
                                    content_parts.append(param_info)
                            
                            # 提取返回值
                            returns = self._extract_returns(dd)
                            if returns and (returns.get('type') or returns.get('description')):
                                content_parts.append("\nReturns:")
                                if returns.get('type'):
                                    content_parts.append(f"  Type: {returns['type']}")
                                if returns.get('description'):
                                    content_parts.append(f"  {returns['description']}")
        except Exception:
            pass
        
        return '\n'.join(content_parts) if content_parts else ''

    def extract_class_content(self, url: str, api_name: str) -> Optional[Dict[str, Any]]:
        """提取整个类的文档内容"""
        try:
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # 提取基类
            base_classes = self._extract_base_classes(soup)

            # 提取方法描述（从页面开头的表格）
            method_descriptions = self._extract_methods_descriptions(soup)

            # 提取方法列表（包含描述）
            methods = self._extract_methods_list(soup, method_descriptions)

            # 提取完整内容
            content = self._extract_full_class_content(soup)

            return {
                'api_name': api_name,
                'base_classes': base_classes,
                'methods': methods,
                'content': content,
                'source_url': url
            }

        except Exception:
            return None

    def extract_by_method_name(self, api_name: str) -> Optional[Dict[str, Any]]:
        """
        根据方法名称或类名提取内容（自动尝试不同分类）

        Args:
            api_name: 如 "QgsRasterBlock.noDataValue" 或 "QgsRasterBlock"

        Returns:
            提取的内容字典，如果所有分类都失败返回 None
        """
        # 解析方法名称
        parsed = self.parse_method_name(api_name)
        print(parsed)
        if not parsed:
            return None

        class_name = parsed['class_name']
        method_name_part = parsed['method_name']

        # 尝试不同的分类
        for category in self.CATEGORIES:
            url = self.build_url(category, class_name)

            # 判断是提取类还是提取方法
            if method_name_part is None:
                # 提取整个类
                content = self.extract_class_content(url, api_name)
            else:
                # 提取单个方法
                content = self.extract_method_content(url, class_name, method_name_part)
            
            if content:
                return content

        return None


    def batch_extract(self, api_names: list, output_file: Optional[str] = None) -> list:
        """
        批量提取多个方法的内容

        Args:
            api_names: 方法名称列表
            output_file: 输出文件路径（可选）

        Returns:
            提取成功的所有结果列表
        """
        results = []
        summary = {
            "total": len(api_names),
            "success": 0,
            "failed": 0,
            "failed_apis": []
        }

        for api_name in api_names:
            result = self.extract_by_method_name(api_name)

            if result:
                results.append(result)
                summary["success"] += 1
            else:
                summary["failed"] += 1
                summary["failed_apis"].append(api_name)

        if output_file:
            output_data = {
                "summary": summary,
                "results": results
            }
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(output_data, f, ensure_ascii=False, indent=2)
            print(f"Results saved to: {output_file}")

        return results


def search_gdal_docs(method_names: List[str]) -> List[Dict[str, Any]]:
    """Search GDAL API docs by method names."""
    if not method_names:
        return []
    
    # 自动补全 osgeo 前缀
    normalized_names = []
    for query in method_names:
        if query.startswith(('gdal.', 'ogr.', 'osr.')) and not query.startswith('osgeo.'):
            normalized_names.append(f'osgeo.{query.lower()}')
        else:
            normalized_names.append(query.lower())
    
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                # 统一大小写查询，原始名称返回
                cur.execute(
                    """SELECT api_name, description, params, example_code, doc_type as library
                       FROM rag_docs
                       WHERE LOWER(api_name) = ANY(%s) AND doc_type = 'gdal'
                       ORDER BY api_name""",
                    (normalized_names,)
                )
                docs = cur.fetchall()
                logger.info(f"Found {len(docs)} GDAL docs for {len(method_names)} queries")
                return docs
    except Exception as e:
        logger.error(f"GDAL search failed: {e}")
        return []
    


def search_cookbook(user_intent: str, similarity_threshold: float = 0.8, top_k: int = 3) -> List[Dict[str, Any]]:
    """Search cookbook for similar examples using pg_trgm similarity."""
    if not user_intent:
        return []
    
    try:
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """SELECT id, user_intent, verified_code, tags, complexity_score, steps, usage_count,
                              similarity(user_intent, %s) as similarity_score
                       FROM cookbook
                       WHERE similarity(user_intent, %s) > %s
                       ORDER BY similarity(user_intent, %s) DESC
                       LIMIT %s""",
                    (user_intent, user_intent, similarity_threshold, user_intent, top_k)
                )
                results = cur.fetchall()
                logger.info(f"Found {len(results)} cookbook examples (threshold>{similarity_threshold})")
                return results
    except Exception as e:
        logger.error(f"Cookbook search failed: {e}")
        return []

mcp = FastMCP("PyQGIS_Doc_Extractor")
_pyqgis_extractor = QGISMethodExtractor()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/qgis_db")
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)


@mcp.tool()
def search_pyqgis_api(ctx: Context, api_names: list[str], output_file: str | None = None) -> str:
    """
    Batch extract PyQGIS docs for classes/methods.

    Args:
        api_names: List like ["QgsRasterBlock.noDataValue", "QgsProject"]
        output_file: JSON output file path
    """
    results = _pyqgis_extractor.batch_extract(api_names, output_file=output_file)
    return json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2)


@mcp.tool()
def search_gdal_api(ctx: Context, api_names: list[str]) -> str:
    """
    Search GDAL API documentation by method names.
    
    Args:
        api_names: List of GDAL method names (e.g., ['osgeo.gdal.Warp', 'osgeo.ogr.Layer.CreateField'])
    
    Returns:
        JSON string with matched GDAL API documentation
    """
    results = search_gdal_docs(api_names)
    return json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2)


@mcp.tool()
def search_qgis_cookbook(ctx: Context, user_intent: str, similarity_threshold: float = 0.8, top_k: int = 3) -> str:
    """
    Search QGIS cookbook for similar examples.
    
    Args:
        user_intent: User intent description
        similarity_threshold: Similarity threshold (0.0-1.0), default 0.8
        top_k: Return top k results, default 3
    
    Returns:
        JSON string with similar cookbook examples
    """
    results = search_cookbook(user_intent, similarity_threshold, top_k)
    return json.dumps({"count": len(results), "results": results}, ensure_ascii=False, indent=2)


def main():
    """Run the MCP server"""
    uvicorn.run(mcp.streamable_http_app, host="0.0.0.0", port=8002)


if __name__ == '__main__':
    main()
