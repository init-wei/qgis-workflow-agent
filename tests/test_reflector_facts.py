"""Completion facts must survive stale model text and remain separate outcomes."""
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from agent.nodes import reflector_node as module
from agent.planning.plan_schema import PlanStep, TaskPlan, ToolCallPlan
from agent.state import create_initial_state
from agent.tools.tool_contracts import ExecutionTrace, StepAttempt, ToolObservation


def state_with_result(*, confirmed=True, outcome='completed', attempt_status='success', spatial='passed'):
    state = create_initial_state('session', '加载当前文件')
    call = ToolCallPlan(step_id=1, tool_name='add_layer', arguments={
        'path': '/controlled/input.geojson', 'layer_type': 'vector', 'name': '完整 当前名称',
    })
    state['task_plan'] = TaskPlan(task_id=state['task_id'], revision=2,
        task_summary='批准的当前名称', review_status='approved', risk_level='low',
        steps=[PlanStep(step_id=1, description='加载', risk_level='low', requires_review=False,
                        allowed_tools=['add_layer'])], tool_calls=[call])
    trace = ExecutionTrace(trace_id='trace', task_id=state['task_id'])
    trace.append_attempt(StepAttempt(attempt_id='attempt', step_id=1, tool_call=call,
        observation=ToolObservation(tool_name='add_layer', status='success' if outcome=='completed' else 'disconnected',
            outcome=outcome, data={'name':'完整 当前名称','source':'/controlled/input.geojson','feature_count':3}),
        status=attempt_status, started_at=datetime(2026,10,31,tzinfo=UTC),
        finished_at=datetime(2026,10,31,tzinfo=UTC), duration_ms=0,
        artifacts=[{'kind':'layer','path':'/controlled/input.geojson', 'deliverable':spatial=='passed',
                    'spatial_validation':{'status':spatial}}]))
    state.update(execution_trace=trace, is_completed=confirmed,
                 messages=[AIMessage(content='旧修订：名称为旧错误名称，未执行。')])
    return state


def stale_model(monkeypatch):
    calls=[]
    class OldModel:
        async def ainvoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content='已经成功加载旧错误名称，3个文件全部交付。')
    monkeypatch.setattr(module, '_get_llm', lambda state: OldModel(), raising=False)
    return calls


def test_completion_uses_current_plan_and_attempt_not_stale_model(monkeypatch):
    calls=stale_model(monkeypatch)
    state=state_with_result()
    result=asyncio.run(module.reflector_node(state))
    text=result['final_summary']
    assert '完整 当前名称' in text and state['task_id'] in text and '修订：2' in text
    assert '旧错误名称' not in text and '3个文件全部交付' not in text
    assert '工具完成：1/1' in text and '用户确认：成功' in text and '空间验证：1 通过' in text
    assert calls==[]
    assert result['task_plan'] is None and result['execution_trace'] is None
    assert result['history'][0]['task_plan']['revision']==2
    assert result['history'][0]['final_summary']==text


@pytest.mark.parametrize('confirmed,label',[(True,'成功'),(False,'失败'),(None,'未确认')])
def test_confirmation_is_separate_from_actual_tool_success(monkeypatch,confirmed,label):
    stale_model(monkeypatch)
    result=asyncio.run(module.reflector_node(state_with_result(confirmed=confirmed)))
    assert '工具完成：1/1' in result['final_summary']
    assert f'用户确认：{label}' in result['final_summary']
    assert result['is_completed'] is confirmed


@pytest.mark.parametrize('outcome,status,label',[
    ('unknown','failed','结果未知，需核查'),('not_started','blocked','未完成或失败'),
])
def test_user_confirmation_cannot_convert_unknown_or_unstarted_to_success(monkeypatch,outcome,status,label):
    stale_model(monkeypatch)
    text=asyncio.run(module.reflector_node(state_with_result(outcome=outcome,attempt_status=status)))['final_summary']
    assert '工具完成：0/1' in text and label in text and '用户确认：成功' in text


def test_tool_completion_does_not_hide_spatial_failure(monkeypatch):
    stale_model(monkeypatch)
    state=state_with_result(spatial='failed')
    state['execution_trace'].status='failed'
    state['execution_trace'].attempts[0].status='failed'
    text=asyncio.run(module.reflector_node(state))['final_summary']
    assert '工具完成：1/1' in text and '空间验证：0 通过，1 失败' in text
    assert '可交付产物：0/1' in text and '执行状态：未完成或失败' in text


def test_missing_or_foreign_trace_is_unknown_not_success(monkeypatch):
    stale_model(monkeypatch)
    state=state_with_result()
    state['execution_trace'].task_id='another-task'
    text=asyncio.run(module.reflector_node(state))['final_summary']
    assert '执行记录：缺失或任务不匹配' in text and '工具完成：未知' in text
    state['execution_trace']=None
    assert '工具完成：未知' in asyncio.run(module.reflector_node(state))['final_summary']


def test_checkpoint_dicts_produce_same_facts_as_graph_models(monkeypatch):
    stale_model(monkeypatch)
    state=state_with_result()
    expected=asyncio.run(module.reflector_node(state))['final_summary']
    state['task_plan']=state['task_plan'].model_dump(mode='json')
    state['execution_trace']=state['execution_trace'].model_dump(mode='json')
    assert asyncio.run(module.reflector_node(state))['final_summary']==expected


def test_absent_confirmation_is_unknown_not_explicit_null(monkeypatch):
    stale_model(monkeypatch)
    state=state_with_result()
    del state['is_completed']
    assert '用户确认：未知' in asyncio.run(module.reflector_node(state))['final_summary']


def test_successful_read_response_has_no_write_outcome_and_is_counted(monkeypatch):
    from agent.tools.mcp_response_parser import MCPResponseParser
    stale_model(monkeypatch)
    state=state_with_result()
    call=ToolCallPlan(step_id=1,tool_name='get_layers',arguments={})
    state['task_plan'].tool_calls=[call]
    state['task_plan'].steps[0].allowed_tools=['get_layers']
    attempt=state['execution_trace'].attempts[0]
    attempt.tool_call=call
    attempt.observation=MCPResponseParser().parse('get_layers',{'status':'success','result':[]})
    attempt.artifacts=[]
    assert attempt.observation.outcome is None
    text=asyncio.run(module.reflector_node(state))['final_summary']
    assert '工具完成：1/1' in text and 'success / 只读响应' in text


def test_missing_write_outcome_is_not_assumed_completed(monkeypatch):
    stale_model(monkeypatch)
    state=state_with_result()
    state['execution_trace'].attempts[0].observation.outcome=None
    text=asyncio.run(module.reflector_node(state))['final_summary']
    assert '工具完成：0/1' in text and '结果未知，需核查' in text


def test_blocked_risk_attempt_was_not_sent_and_is_not_unknown_write(monkeypatch):
    stale_model(monkeypatch)
    state=state_with_result(outcome=None,attempt_status='blocked')
    text=asyncio.run(module.reflector_node(state))['final_summary']
    assert '尝试/审核记录' in text and 'blocked / 未发送' in text
    assert '结果未知，需核查' not in text and '实际调用' not in text
    assert '工具完成：0/1' in text and '未完成或失败' in text
