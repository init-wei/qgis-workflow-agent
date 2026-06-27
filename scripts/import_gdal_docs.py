#!/usr/bin/env python3
"""
GDAL文档导入脚本
将GDAL JSON文档导入rag_docs表
"""

import json
import os
from pathlib import Path
from psycopg_pool import ConnectionPool
from psycopg.rows import dict_row
from dotenv import load_dotenv
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 加载环境变量
load_dotenv()

# 数据库连接配置
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/qgis_db")

# GDAL文档目录
GDAL_DOCS_DIR = os.getenv("GDAL_DOCS_DIR", "./data/gdal_docs")

# 创建连接池
pool = ConnectionPool(conninfo=DATABASE_URL, min_size=1, max_size=10)


def load_gdal_docs(directory):
    """加载GDAL文档JSON文件"""
    
    docs = []
    docs_path = Path(directory)
    
    if not docs_path.exists():
        logger.warning(f"GDAL文档目录不存在: {docs_path.absolute()}")
        logger.info("正在创建目录...")
        docs_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"✅ 目录已创建: {docs_path.absolute()}")
        return docs
    
    # 查找所有JSON文件
    json_files = list(docs_path.glob("*.json"))
    
    if not json_files:
        logger.warning(f"未找到JSON文件: {docs_path.absolute()}")
        logger.info("提示: 请将GDAL文档的JSON文件放入此目录")
        return docs
    
    # 读取所有JSON文件
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # 如果是单个文档对象
                if isinstance(data, dict):
                    docs.append(data)
                # 如果是文档数组
                elif isinstance(data, list):
                    docs.extend(data)
                
                logger.info(f"✅ 加载文件: {json_file.name}")
        
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON解析失败: {json_file.name} - {e}")
        except Exception as e:
            logger.error(f"❌ 读取文件失败: {json_file.name} - {e}")
    
    logger.info(f"总共加载 {len(docs)} 条GDAL文档")
    return docs


def import_gdal_docs_to_database(docs):
    """将GDAL文档导入数据库"""
    
    if not docs:
        logger.warning("没有文档可导入")
        return
    
    with pool.connection() as conn:
        with conn.cursor() as cur:
            
            success_count = 0
            skip_count = 0
            error_count = 0
            
            for doc in docs:
                try:
                    # 提取文档字段
                    doc_type = doc.get('doc_type', 'gdal')
                    
                    # 组合 classname + method 作为 api_name
                    classname = doc.get('classname', '')
                    method = doc.get('method', '')
                    
                    # 如果已经有 api_name，直接使用；否则从 classname 和 method 组合
                    if doc.get('api_name'):
                        api_name = doc.get('api_name')
                    elif classname and method:
                        # 如果 method 已经包含 classname，直接使用 method
                        if method.startswith(classname):
                            api_name = method
                        # 如果 classname 为空，只用 method
                        elif not classname:
                            api_name = method
                        # 否则组合 classname.method
                        else:
                            api_name = f"{classname}.{method}"
                    elif method:
                        api_name = method
                    elif classname:
                        api_name = classname
                    else:
                        api_name = doc.get('name', '')
                    
                    category = doc.get('category')
                    signature = doc.get('signature')
                    params = doc.get('params') or doc.get('parameters')
                    returns = doc.get('returns') or doc.get('return_type')
                    description = doc.get('description')
                    example_code = doc.get('example_code') or doc.get('example')
                    source_url = doc.get('source_url') or doc.get('url')
                    
                    # 验证必需字段
                    if not api_name:
                        logger.warning(f"⚠️ 跳过无效文档（缺少classname/method）: {doc}")
                        skip_count += 1
                        continue
                    
                    # 将params转换为纯字符串格式（每个参数一行）
                    if params:
                        if isinstance(params, list):
                            # 如果是数组，用换行符连接
                            params_str = '\n'.join(params)
                        elif isinstance(params, str):
                            # 如果已经是字符串，直接使用
                            params_str = params
                        else:
                            # 其他类型（如dict），转为JSON字符串
                            params_str = json.dumps(params, ensure_ascii=False, indent=2)
                    else:
                        params_str = None
                    
                    # 插入数据（使用ON CONFLICT DO UPDATE处理重复）
                    cur.execute("""
                        INSERT INTO rag_docs (
                            doc_type, api_name, category, signature, 
                            params, returns, description, example_code, source_url
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (doc_type, api_name) 
                        DO UPDATE SET
                            category = EXCLUDED.category,
                            signature = EXCLUDED.signature,
                            params = EXCLUDED.params,
                            returns = EXCLUDED.returns,
                            description = EXCLUDED.description,
                            example_code = EXCLUDED.example_code,
                            source_url = EXCLUDED.source_url,
                            updated_at = CURRENT_TIMESTAMP;
                    """, (
                        doc_type, api_name, category, signature,
                        params_str, returns, description, example_code, source_url
                    ))
                    
                    success_count += 1
                    
                    if success_count % 10 == 0:
                        logger.info(f"已导入 {success_count} 条文档...")
                
                except Exception as e:
                    logger.error(f"❌ 导入文档失败: {doc.get('api_name', 'unknown')} - {e}")
                    error_count += 1
            
            # 提交事务
            conn.commit()
            
            logger.info("\n" + "=" * 60)
            logger.info(f"导入统计:")
            logger.info(f"  ✅ 成功: {success_count} 条")
            logger.info(f"  ⚠️ 跳过: {skip_count} 条")
            logger.info(f"  ❌ 失败: {error_count} 条")
            logger.info("=" * 60)


def verify_import():
    """验证导入结果"""
    
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            
            # 统计总文档数
            cur.execute("SELECT COUNT(*) as total FROM rag_docs;")
            result = cur.fetchone()
            total = result['total'] if result else 0
            
            logger.info(f"\n数据库中的文档总数: {total}")
            
            # 按doc_type统计
            cur.execute("""
                SELECT doc_type, COUNT(*) as count 
                FROM rag_docs 
                GROUP BY doc_type 
                ORDER BY count DESC;
            """)
            
            type_stats = cur.fetchall()
            if type_stats:
                logger.info(f"\n按类型统计:")
                for stat in type_stats:
                    logger.info(f"  - {stat['doc_type']}: {stat['count']} 条")
            
            # 按category统计（取前10）
            cur.execute("""
                SELECT category, COUNT(*) as count 
                FROM rag_docs 
                WHERE category IS NOT NULL
                GROUP BY category 
                ORDER BY count DESC
                LIMIT 10;
            """)
            
            category_stats = cur.fetchall()
            if category_stats:
                logger.info(f"\n按分类统计（前10）:")
                for stat in category_stats:
                    logger.info(f"  - {stat['category']}: {stat['count']} 条")
            
            # 显示示例文档
            cur.execute("""
                SELECT api_name, category, doc_type 
                FROM rag_docs 
                ORDER BY created_at DESC 
                LIMIT 5;
            """)
            
            samples = cur.fetchall()
            if samples:
                logger.info(f"\n示例文档（最新5条）:")
                for sample in samples:
                    logger.info(f"  - [{sample['doc_type']}] {sample['api_name']} ({sample['category']})")


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("开始导入GDAL文档...")
    logger.info("=" * 60)
    
    try:
        # 加载文档
        docs = load_gdal_docs(GDAL_DOCS_DIR)
        
        # 导入数据库
        import_gdal_docs_to_database(docs)
        
        # 验证结果
        verify_import()
        
        logger.info("\n" + "=" * 60)
        logger.info("✅ GDAL文档导入完成！")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"❌ GDAL文档导入失败: {e}")
        raise
    finally:
        pool.close()
