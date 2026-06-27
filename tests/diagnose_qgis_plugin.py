#!/usr/bin/env python3
"""
QGIS插件诊断脚本
直接连接到QGIS Socket测试命令
"""

import socket
import json
import sys

def test_qgis_command(command_type, params=None):
    """测试QGIS命令"""
    host = 'localhost'
    port = 9876
    
    # 连接到QGIS
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, port))
        print(f"✅ 成功连接到 QGIS ({host}:{port})")
    except Exception as e:
        print(f"❌ 无法连接到 QGIS: {e}")
        print("请确保：")
        print("  1. QGIS 已启动")
        print("  2. QGIS MCP 插件已加载")
        print("  3. MCP Server 已在插件中启动")
        sys.exit(1)
    
    # 构建命令
    command = {
        "type": command_type,
        "params": params or {}
    }
    
    print(f"\n发送命令: {command_type}")
    print(f"参数: {json.dumps(params, indent=2, ensure_ascii=False)}")
    
    # 发送命令
    try:
        sock.sendall(json.dumps(command).encode('utf-8'))
        
        # 接收响应
        response_data = b''
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response_data += chunk
            
            # 尝试解析JSON
            try:
                json.loads(response_data.decode('utf-8'))
                break
            except json.JSONDecodeError:
                continue
        
        # 解析响应
        response = json.loads(response_data.decode('utf-8'))
        print(f"\n响应:")
        print(json.dumps(response, indent=2, ensure_ascii=False))
        
        return response
        
    except Exception as e:
        print(f"❌ 命令执行错误: {e}")
        return None
    finally:
        sock.close()

def main():
    print("=" * 60)
    print("QGIS 插件诊断工具")
    print("=" * 60)
    
    # 测试1: Ping
    print("\n【测试1】Ping")
    print("-" * 60)
    result = test_qgis_command("ping")
    if result and result.get("status") == "success":
        print("✅ Ping 成功")
    else:
        print("❌ Ping 失败")
    
    # 测试2: Get QGIS Info
    print("\n【测试2】Get QGIS Info")
    print("-" * 60)
    result = test_qgis_command("get_qgis_info")
    if result and result.get("status") == "success":
        print("✅ Get QGIS Info 成功")
    else:
        print("❌ Get QGIS Info 失败")
    
    # 测试3: Execute Code
    print("\n【测试3】Execute Code")
    print("-" * 60)
    code = """
print("测试代码执行")
from qgis.core import Qgis
print(f"QGIS版本: {Qgis.QGIS_VERSION}")
"""
    result = test_qgis_command("execute_code", {"code": code})
    if result and result.get("status") == "success":
        print("✅ Execute Code 成功")
    else:
        print("❌ Execute Code 失败")
    
    # 测试4: Capture Map Canvas
    print("\n【测试4】Capture Map Canvas")
    print("-" * 60)
    import os
    test_path = os.path.abspath("shared/screenshots/diagnose_test.png")
    result = test_qgis_command("capture_map_canvas", {
        "path": test_path,
        "width": 800,
        "height": 600
    })
    if result and result.get("status") == "success":
        print("✅ Capture Map Canvas 成功")
        if os.path.exists(test_path):
            print(f"✅ 截图文件已创建: {test_path}")
        else:
            print(f"⚠️ 命令成功但文件未找到: {test_path}")
    else:
        print("❌ Capture Map Canvas 失败")
        if result:
            print(f"错误信息: {result.get('message', '未知错误')}")
    
    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)

if __name__ == "__main__":
    main()
