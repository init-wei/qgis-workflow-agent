import pytest


def fake_spatial_inspector(request):
    """GIS seam for workflow tests that intentionally use synthetic input bytes."""
    review_steps = []
    if request.get('mode') == 'preflight':
        plan = request['plan']
        steps = {step['step_id']: step for step in plan['steps']}
        for call in plan['tool_calls']:
            tool, args = call['tool_name'], call['arguments']
            if tool != 'execute_processing' and not (tool == 'add_layer' and args.get('layer_type') == 'vector'):
                continue
            algorithm = args.get('algorithm')
            parameters = args.get('parameters', {})
            table = algorithm in {'native:basicstatisticsforfields', 'qgis:statisticsbycategories'}
            field = (parameters.get('FIELD_NAME') if algorithm == 'native:basicstatisticsforfields'
                     else parameters.get('VALUES_FIELD_NAME') if algorithm == 'qgis:statisticsbycategories'
                     else 'area' if algorithm == 'qgis:exportaddgeometrycolumns' else None)
            groups = (parameters.get('FIELD') if algorithm == 'native:dissolve'
                      else parameters.get('CATEGORIES_FIELD_NAME') if algorithm == 'qgis:statisticsbycategories'
                      else [])
            review_steps.append({
                'step_id': call['step_id'], 'tool': tool, 'algorithm': algorithm,
                'input_crs': [{'ref': ref, 'crs': 'EPSG:32650'} for ref in steps[call['step_id']]['input_refs']],
                'output_crs': None if table else 'EPSG:32650', 'input_kind': 'spatial',
                'output_kind': 'table' if table else 'layer' if tool == 'add_layer' else 'spatial',
                'geometry': None if table else 'Polygon', 'field': field, 'field_type': 'Real' if field else None,
                'group_fields': groups,
                'unit': 'm2' if algorithm == 'qgis:exportaddgeometrycolumns' else 'm' if algorithm == 'native:buffer' else None,
            })
    return {'status': 'passed', 'checks': ['fake_gis'], 'errors': [],
            'facts': {'review_steps': review_steps} if request.get('mode') == 'preflight' else {}}


@pytest.fixture
def mock_mcp_responses():
    return {
        "success": {"status": "success", "result": {"path": "shared/output/map.png"}},
        "parameter_error": {
            "status": "error",
            "message": "Missing required parameter: path",
        },
        "missing_file": {
            "status": "error",
            "message": "Layer is not valid: data/missing.gpkg",
        },
        "disconnect": ConnectionError(
            "Could not connect to Qgis. Make sure the Qgis plugin is running."
        ),
    }
