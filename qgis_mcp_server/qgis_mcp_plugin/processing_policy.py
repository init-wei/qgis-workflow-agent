"""Processing algorithms that the Agent may ask the local QGIS plugin to run."""

SUPPORTED_PROCESSING_ALGORITHMS = frozenset(
    {
        "native:reprojectlayer",
        "native:buffer",
        "native:clip",
        "native:dissolve",
        "qgis:statisticsbycategories",
        "native:basicstatisticsforfields",
        "qgis:exportaddgeometrycolumns",
    }
)


def validate_processing_request(algorithm, parameters):
    """Reject unsupported algorithms and absent parameter objects before execution."""
    if not isinstance(algorithm, str) or algorithm not in SUPPORTED_PROCESSING_ALGORITHMS:
        raise ValueError(f"Processing algorithm is not supported: {algorithm!r}")
    if not isinstance(parameters, dict) or not parameters:
        raise ValueError("Processing parameters must be a non-empty object")
    if algorithm in {"native:clip", "native:dissolve", "qgis:exportaddgeometrycolumns",
                     "native:basicstatisticsforfields", "qgis:statisticsbycategories"}:
        output = parameters.get("OUTPUT")
        if not isinstance(output, str) or not output.lower().endswith(".gpkg"):
            raise ValueError("This operation requires an explicit GeoPackage OUTPUT")
    if algorithm == "native:dissolve":
        fields = parameters.get("FIELD")
        if (not isinstance(fields, list)
                or any(not isinstance(field, str) or not field.strip() for field in fields)
                or len(set(fields)) != len(fields)):
            raise ValueError("Dissolve requires explicit FIELD list (empty means all features)")
        if type(parameters.get("SEPARATE_DISJOINT")) is not bool:
            raise ValueError("Dissolve requires explicit SEPARATE_DISJOINT boolean")
    elif algorithm == "qgis:exportaddgeometrycolumns":
        if type(parameters.get("CALC_METHOD")) is not int or parameters["CALC_METHOD"] != 0:
            raise ValueError("Geometry columns require CALC_METHOD=0")
    elif algorithm in {"native:basicstatisticsforfields", "qgis:statisticsbycategories"}:
        if algorithm == "native:basicstatisticsforfields":
            if not isinstance(parameters.get("FIELD_NAME"), str) or not parameters["FIELD_NAME"].strip():
                raise ValueError("Basic statistics require a numeric FIELD_NAME")
            if "OUTPUT_HTML_FILE" in parameters:
                raise ValueError("Statistics HTML output is not supported")
        else:
            fields = parameters.get("CATEGORIES_FIELD_NAME")
            if (not isinstance(fields, list) or not fields
                    or any(not isinstance(field, str) or not field.strip() for field in fields)
                    or len(set(fields)) != len(fields)):
                raise ValueError("Categorical statistics require non-empty CATEGORIES_FIELD_NAME list")
            value_field = parameters.get("VALUES_FIELD_NAME")
            if "VALUES_FIELD_NAME" in parameters and (
                not isinstance(value_field, str) or not value_field.strip()
            ):
                raise ValueError("VALUES_FIELD_NAME must be a non-empty numeric field when provided")


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
