"""Processing algorithms that the Agent may ask the local QGIS plugin to run."""

SUPPORTED_PROCESSING_ALGORITHMS = frozenset(
    {
        "native:reprojectlayer",
        "native:buffer",
        "native:clip",
        "native:dissolve",
        "qgis:statisticsbycategories",
        "native:basicstatisticsforfields",
    }
)


def validate_processing_request(algorithm, parameters):
    """Reject unsupported algorithms and absent parameter objects before execution."""
    if not isinstance(algorithm, str) or algorithm not in SUPPORTED_PROCESSING_ALGORITHMS:
        raise ValueError(f"Processing algorithm is not supported: {algorithm!r}")
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("Processing parameters must be a non-empty object")


def missing_required_parameters(parameters, definitions):
    """Return required algorithm parameters omitted by the caller.

    QGIS owns the parameter schema. This keeps the MCP boundary from guessing
    version-specific parameter names or silently accepting QGIS defaults.
    """
    missing = []
    for definition in definitions:
        flags = definition.flags()
        optional_flag, hidden_flag = _parameter_flags(definition)
        if flags & hidden_flag:
            continue
        if not flags & optional_flag and definition.name() not in parameters:
            missing.append(definition.name())
    return sorted(missing)


def _parameter_flags(definition):
    """Read optional/hidden bits from the QGIS parameter definition flags enum."""
    definition_type = type(definition)
    optional_flag = getattr(definition_type, "FlagOptional", None)
    hidden_flag = getattr(definition_type, "FlagHidden", None)
    if optional_flag is None or hidden_flag is None:
        try:
            from qgis.core import QgsProcessingParameterDefinition
        except ImportError as exc:
            raise RuntimeError("QGIS processing parameter flags are unavailable") from exc
        optional_flag = QgsProcessingParameterDefinition.FlagOptional
        hidden_flag = QgsProcessingParameterDefinition.FlagHidden
    return optional_flag, hidden_flag


def validate_required_parameters(parameters, definitions):
    """Raise when a required QGIS algorithm parameter was omitted."""
    missing = missing_required_parameters(parameters, definitions)
    if missing:
        raise ValueError("Missing required processing parameters: " + ", ".join(missing))


def validate_parameter_values(parameters, definitions, context):
    """Validate JSON types and values using the running QGIS definitions, before any write.

    Source paths may name preceding planned outputs. Their references are checked
    by the application; this preflight deliberately does not require them to exist.
    """
    validate_required_parameters(parameters, definitions)
    definitions_by_name = {definition.name(): definition for definition in definitions}
    unknown = set(parameters) - definitions_by_name.keys()
    if unknown:
        raise ValueError('Unknown processing parameters: ' + ', '.join(sorted(unknown)))
    source_paths = []
    output_paths = []
    for name, value in parameters.items():
        definition = definitions_by_name[name]
        kind = definition.type()
        metadata = definition.toVariantMap()
        optional_flag, _ = _parameter_flags(definition)
        if value is None and definition.flags() & optional_flag:
            continue
        if kind in {'source', 'sink', 'fileDestination', 'crs', 'coordinateoperation'}:
            valid_type = isinstance(value, str) and bool(value)
        elif kind == 'boolean':
            valid_type = isinstance(value, bool)
        elif kind in {'distance', 'number'}:
            valid_type = type(value) in {int, float}
            if metadata.get('data_type') == 0:
                valid_type = type(value) is int
        elif kind == 'enum':
            values = value if metadata.get('allow_multiple') and isinstance(value, list) else [value]
            valid_type = all(type(item) is int for item in values)
        elif kind == 'field':
            valid_type = isinstance(value, str) or (
                metadata.get('allow_multiple') and isinstance(value, list)
                and all(isinstance(item, str) for item in value)
            )
        else:
            raise ValueError(f'Unsupported QGIS parameter definition: {name} ({kind})')
        if not valid_type:
            raise ValueError(f'Invalid JSON type for QGIS parameter {name} ({kind})')
        if kind == 'source':
            source_paths.append(value)
        elif kind in {'sink', 'fileDestination'}:
            output_paths.append(value)
        elif not definition.checkValueIsAcceptable(value, context):
            raise ValueError(f'Invalid value for QGIS parameter {name}')
    return {'source_paths': source_paths, 'output_paths': output_paths}
