#!/usr/bin/env python3
"""
Database工具测试
"""

from agent.tools.database import (
    save_execution_log,
    get_execution_logs,
    save_cookbook_entry,
    update_cookbook_usage,
    get_cookbook_by_id
)


def test_save_and_get_execution_log():
    """测试保存和获取执行日志"""
    session_id = "test_db_001"
    
    # 保存日志
    result = save_execution_log(
        session_id=session_id,
        step_id=1,
        message="测试日志",
        status="info"
    )
    
    assert result is True
    
    # 获取日志
    logs = get_execution_logs(session_id, limit=10)
    
    assert len(logs) > 0
    assert logs[0]["session_id"] == session_id
    assert logs[0]["message"] == "测试日志"
    
    print("✅ 执行日志保存和获取测试通过")


def test_save_cookbook_entry():
    """测试保存Cookbook条目"""
    entry_id = save_cookbook_entry(
        user_intent="测试意图：加载图层",
        verified_code="# 测试代码\nprint('hello')",
        tags=["test", "layer"],
        complexity_score=0.5
    )
    
    assert entry_id is not None
    assert isinstance(entry_id, int)
    
    print(f"✅ Cookbook条目保存测试通过 (ID: {entry_id})")
    
    # 测试获取
    entry = get_cookbook_by_id(entry_id)
    assert entry is not None
    assert entry["user_intent"] == "测试意图：加载图层"
    
    print("✅ Cookbook条目获取测试通过")
    
    # 测试更新使用次数
    result = update_cookbook_usage(entry_id)
    assert result is True
    
    print("✅ Cookbook使用次数更新测试通过")


if __name__ == "__main__":
    print("运行Database工具测试...")
    print("注意: 需要PostgreSQL数据库运行")
    print("-" * 60)
    
    try:
        test_save_and_get_execution_log()
        test_save_cookbook_entry()
        
        print("\n" + "=" * 60)
        print("所有Database测试通过！")
        print("=" * 60)
    
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
