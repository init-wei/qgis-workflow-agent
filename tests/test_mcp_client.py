#!/usr/bin/env python3
"""
MCP客户端测试
"""

import asyncio
from agent.tools.mcp_client import QGISMCPClient, get_mcp_client


async def test_mcp_client_connection():
    """测试MCP客户端连接"""
    print("测试MCP客户端连接...")
    
    async with QGISMCPClient() as client:
        assert client.session_id is not None
        print(f"✅ 连接成功, session_id: {client.session_id}")


async def test_execute_code():
    """测试代码执行"""
    print("\n测试代码执行...")
    
    async with QGISMCPClient() as client:
        code = """
print("Hello from QGIS!")
from qgis.core import Qgis
print(f"QGIS版本: {Qgis.QGIS_VERSION}")
"""
        
        result = await client.execute_code(code)
        print(f"执行结果: {result}")
        
        assert "result" in result
        print("✅ 代码执行测试通过")


async def test_capture_screenshot():
    """测试截图功能"""
    print("\n测试截图功能...")
    
    import os
    
    async with QGISMCPClient() as client:
        screenshot_path = os.path.abspath("tests/test_screenshot.png")
        
        result = await client.capture_screenshot(
            path=screenshot_path,
            width=800,
            height=600
        )
        
        print(f"截图结果: {result}")
        
        # 检查文件是否存在
        if os.path.exists(screenshot_path):
            print(f"✅ 截图文件已创建: {screenshot_path}")
        else:
            print("⚠️ 截图文件未创建（可能QGIS没有打开项目）")


async def test_global_client():
    """测试全局客户端单例"""
    print("\n测试全局客户端单例...")
    
    client1 = await get_mcp_client()
    client2 = await get_mcp_client()
    
    assert client1 is client2
    assert client1.session_id == client2.session_id
    
    print("✅ 全局客户端单例测试通过")


async def main():
    """运行所有测试"""
    print("运行MCP客户端测试...")
    print("注意: 需要QGIS MCP插件和MCP Server运行")
    print("=" * 60)
    
    try:
        await test_mcp_client_connection()
        await test_execute_code()
        await test_capture_screenshot()
        await test_global_client()
        
        print("\n" + "=" * 60)
        print("所有MCP客户端测试通过！")
        print("=" * 60)
    
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
