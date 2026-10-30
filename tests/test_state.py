from agent.state import create_initial_state


def test_create_initial_state_has_one_task_plan_and_server_owned_input_context():
    state = create_initial_state("test-session", "Load the selected layer")

    assert state["session_id"] == "test-session"
    assert state["input_query"] == "Load the selected layer"
    assert state["task_id"]
    assert state["uploaded_files"] == []
    assert state["llm_config"] is None
    assert state["retry_count"] == 0
    assert state["task_plan"] is None
    assert state["plan_validation"] is None
    assert state["planning_error"] is None
    assert "plan" not in state
    assert "draft" not in state
    assert "task_plan_draft" not in state
    assert "status" not in state
    assert "tool_selection" not in state
    assert state["messages"] == []
    assert state["history"] == []
    assert state["screenshot_path"] is None
    assert state["execution_trace"] is None
    assert state["final_summary"] is None
    assert state["quality_score"] == 0.0


def test_create_initial_state_accepts_server_resolved_file_references():
    uploaded_file = {
        "file_id": "file-1",
        "filename": "points.geojson",
        "qgis_path": "shared/uploads/file-1.geojson",
        "layer_name": "points",
        "layer_info": {"crs": "EPSG:4326", "geometry": "Point"},
    }

    state = create_initial_state(
        "test-session",
        "Load the selected layer",
        task_id="task-1",
        uploaded_files=[uploaded_file],
    )

    assert state["task_id"] == "task-1"
    assert state["uploaded_files"] == [uploaded_file]
