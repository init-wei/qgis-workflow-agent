#!/usr/bin/env python3
"""
Agent Graph人工测试脚本
演示如何使用HITL (Human-in-the-Loop) 机制
"""

import sys
import os
import json
import asyncio
from typing import Optional

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.graph import get_graph
from agent.state import create_initial_state


def print_plan(plan):
    """打印计划详情"""
    if not plan:
        print("  ❌ 计划为空")
        return
    
    print(f"\n  任务: {plan.task}")
    print(f"\n  执行步骤（共{len(plan.steps)}步）:")
    for step in plan.steps:
        print(f"\n    步骤 {step.step_id}: {step.description}")
        if step.gdal_api:
            print(f"      GDAL API: {', '.join(step.gdal_api)}")
        if step.pyqgis_api:
            print(f"      PyQGIS API: {', '.join(step.pyqgis_api)}")


def review_plan() -> tuple[bool, Optional[str]]:
    """
    让用户审核计划
    
    Returns:
        (是否批准, 修改意见)
    """
    print("\n" + "="*60)
    print("📋 计划审核")
    print("="*60)
    print("\n请选择:")
    print("1. 批准计划 - 继续执行")
    print("2. 修改计划 - 提供修改意见")
    print("3. 放弃任务 - 退出执行")
    
    choice = input("\n请输入选择 (1/2/3): ").strip()
    
    if choice == "1":
        return True, None
    elif choice == "2":
        advise = input("\n请输入修改意见: ").strip()
        return False, advise if advise else "请重新规划"
    else:
        return False, None


def review_result(state):
    """
    让用户审核执行结果
    
    Args:
        state: 当前状态
    """
    print("\n" + "="*60)
    print("📊 执行结果审核")
    print("="*60)
    
    # 显示总结
    if state.get("final_summary"):
        print(f"\n总结:\n{state['final_summary']}")
    
    # 显示截图路径
    if state.get("screenshot_path"):
        print(f"\n截图已保存: {state['screenshot_path']}")
    
    # 显示执行日志
    if state.get("execution_logs"):
        print(f"\n执行日志:")
        for log in state["execution_logs"]:
            status = "✅" if log.get("success") else "❌"
            print(f"  {status} 步骤 {log.get('step_id')}: {log.get('message', '')}")
    
    input("\n按Enter键结束...")


def review_execution() -> bool:
    """让用户确认本次任务是否成功"""
    print("\n" + "="*60)
    print("✅ 执行结果确认")
    print("="*60)
    print("\n请选择:")
    print("1. 任务成功")
    print("2. 任务失败")
    while True:
        choice = input("\n请输入选择 (1/2): ").strip()
        if choice == "1":
            return True
        if choice == "2":
            return False
        print("请输入 1 或 2")


def ask_next_round() -> bool:
    """询问是否继续下一轮对话"""
    print("\n是否开始下一轮对话?")
    print("1. 是")
    print("2. 否")
    while True:
        choice = input("\n请输入选择 (1/2): ").strip()
        if choice == "1":
            return True
        if choice == "2":
            return False
        print("请输入 1 或 2")


async def run_agent(user_query: str, reset_thread: bool = False) -> Optional[dict]:
    """
    运行Agent处理用户查询（支持HITL）

    Args:
        user_query: 用户的GIS任务需求
        reset_thread: 是否重置thread（仅第一轮为True）
    """
    print("\n" + "="*60)
    print("QGIS Agent启动（HITL模式）")
    print("="*60)
    print(f"\n用户查询: {user_query}\n")

    # 1. 构建Graph（启用checkpointer支持HITL）
    print("1. 构建Graph...")
    try:
        # 只在第一轮重置thread，后续轮次保留之前的状态
        reset_thread_id = "demo_thread" if reset_thread else None
        app = await get_graph(
            with_checkpointer=True,
            interrupt_after=["executor_node"],
            reset_thread_id=reset_thread_id
        )
        print("   ✅ Graph构建成功（已启用状态持久化和人工审核）\n")
    except Exception as e:
        print(f"   ❌ Graph构建失败: {e}")
        print("\n提示: 请确保PostgreSQL数据库正在运行")
        return None

    # 2. 准备状态
    print("2. 准备状态...")
    
    config = {
        "configurable": {
            "thread_id": "demo_thread"
        }
    }

    if reset_thread:
        # 情况A：第一轮运行，或者强制重置
        # 需要完整的初始状态
        print("   🆕 初始化新会话状态...")
        current_state = create_initial_state(
            session_id="demo_session",
            input_query=user_query
        )
    else:
        # 情况B：后续轮次
        # 1. 先检查是否存在历史状态（用于打印日志给用户看，不做逻辑依赖）
        try:
            state_snapshot = await app.aget_state(config)
            if state_snapshot and state_snapshot.values:
                log_summary = state_snapshot.values.get("log_summary", "None")
                preview = log_summary[:50] if log_summary else 'None'
                print(f"   ✅ 检测到历史会话")
                print(f"   📝 上一轮摘要: {preview}...")
                
                # 【关键修正】这里只传递增量 (Delta)
                # LangGraph 会自动将其 merge 到数据库中的旧状态
                # 这样 history 字段（operator.add）就不会被自己重复叠加
                current_state = {
                    "input_query": user_query
                }
            else:
                # 理论上不应该走到这里，除非数据库被清空了
                print("   ⚠️ 未检测到历史状态，重新初始化...")
                current_state = create_initial_state(
                    session_id="demo_session",
                    input_query=user_query
                )
        except Exception as e:
            print(f"   ⚠️ 获取状态失败: {e}，将创建新状态")
            current_state = create_initial_state(
                session_id="demo_session",
                input_query=user_query
            )

    # 4. 执行Graph（支持HITL）
    print("\n" + "="*60)
    print("开始执行Graph（HITL模式）")
    print("="*60 + "\n")

    try:
        # 执行流程：Graph会自动管理人工审核流程
        print("🚀 开始执行...\n")

        last_step = -1
        
        # Stream执行，遇到人工审核节点时自动中断
        while True:
            final_state = None
            interrupted = False
            
            async for chunk in app.astream(current_state, config, stream_mode="values"):
                final_state = chunk
                
                # 显示Planner进度
                if chunk.get("draft") and not chunk.get("plan"):
                    print("✓ Planner生成draft完成")
                
                # 显示执行进度
                if "current_step_id" in chunk:
                    current_step = chunk["current_step_id"]
                    if current_step != last_step:
                        last_step = current_step
                        plan = chunk.get("plan")
                        if plan and current_step < len(plan.steps):
                            print(f"⚙️  执行步骤 {current_step + 1}/{len(plan.steps)}: {plan.steps[current_step].description}")
                
                # 检查是否完成API RAG
                if chunk.get("api_context_structured") and not interrupted:
                    print("✓ API RAG节点完成")
                
                # 检查是否到达Reflector
                if chunk.get("final_summary"):
                    print("✓ Reflector节点完成")
            
            # 检查是否在human_review_node前中断
            if final_state and final_state.get("draft") and not final_state.get("status", False):
                interrupted = True
                print("\n⏸️  到达中断点：计划审核")
                print_plan(final_state["draft"])
                
                # 人工审核
                approved, advise = review_plan()
                
                if advise is None and not approved:
                    # 用户选择放弃
                    print("\n任务已取消")
                    return None
                
                if approved:
                    # 批准计划
                    print("\n✅ 计划已批准\n")
                    await app.aupdate_state(
                        config,
                        {
                            "plan": final_state["draft"],
                            "status": True
                        }
                    )
                else:
                    # 需要修改
                    print(f"\n📝 修改意见: {advise}")
                    retry_count = final_state.get("retry_count", 0)
                    await app.aupdate_state(
                        config,
                        {
                            "advise": advise,
                            "status": False,
                            "retry_count": retry_count + 1
                        }
                    )
                    print("🔄 将根据意见重新规划...\n")
                
                # 继续执行
                current_state = None
            # 检查是否在executor_node后中断
            
            elif final_state and final_state.get("messages") and not final_state.get("is_completed"):
                interrupted = True
                print("\n⏸️  到达中断点：执行结果审核")
                is_completed = review_execution()
                await app.aupdate_state(
                    config,
                    {
                        "is_completed": is_completed
                    }
                )
                print("\n✅ 已记录执行结果\n")
                # 继续执行
                current_state = None
            if interrupted:
                continue
            # 没有中断，执行完成
            break
        
        # 显示最终结果
        if final_state:
            review_result(final_state)
        
        print("\n" + "="*60)
        print("执行完成")
        print("="*60)
        return final_state
        
    except Exception as e:
        print(f"\n❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()
        
        print("\n可能的原因:")
        print("- PostgreSQL数据库未运行或连接失败")
        print("- QGIS MCP服务器未运行")
        print("- LLM API密钥未设置或无效")
        print("- GDAL文档未导入到数据库")
        return None


def main():
    """主函数"""
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    print("\n" + "="*60)
    print("QGIS Agent Demo - HITL模式")
    print("="*60)
    print("\nGraph结构:")
    print("  Planner → Human Review → API RAG → Executor → Reflector")
    print("\nHITL暂停点:")
    print("  🔄 在Human Review节点前自动中断，等待人工审核")
    print("  🔄 在Executor节点后自动中断，确认执行结果")
    print("\n功能特性:")
    print("  ✓ 人工审核计划（由Graph自动管理流程）")
    print("  ✓ 提供修改意见")
    print("  ✓ 支持反复修改计划")
    print("  ✓ 实时执行进度显示")
    print("  ✓ 完整结果展示")
    print("="*60 + "\n")
    
    # 示例查询
    examples = [
        "加载一个shapefile并将其重投影到WGS84坐标系",
        "对栅格影像进行裁剪和重采样",
        "创建矢量图层的缓冲区分析",
    ]
    
    print("示例查询:")
    for i, example in enumerate(examples, 1):
        print(f"{i}. {example}")
    
    print("\n请输入您的GIS任务需求（或输入数字选择示例）:")
    user_input = input("> ").strip()
    
    if user_input.isdigit() and 1 <= int(user_input) <= len(examples):
        query = examples[int(user_input) - 1]
    elif user_input:
        query = user_input
    else:
        query = examples[0]
        print(f"使用默认查询: {query}")
    
    # 多轮对话
    current_query = query
    is_first_round = True
    while True:
        final_state = asyncio.run(run_agent(current_query, reset_thread=is_first_round))
        if not final_state:
            break
        if not ask_next_round():
            break
        print("\n请输入下一轮GIS任务需求（或输入数字选择示例）:")
        user_input = input("> ").strip()
        if user_input.isdigit() and 1 <= int(user_input) <= len(examples):
            current_query = examples[int(user_input) - 1]
        elif user_input:
            current_query = user_input
        else:
            current_query = examples[0]
            print(f"使用默认查询: {current_query}")
        is_first_round = False  # 后续轮次不复位thread，保留状态


if __name__ == "__main__":
    main()
