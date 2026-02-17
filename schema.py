"""Convert MCP tool inputSchema to Gemini function declaration format.

Gemini's function calling accepts a subset of JSON Schema. This module
recursively walks an MCP schema and keeps only the fields Gemini
understands, stripping everything else so the API doesn't reject the
declaration.
"""

# Fields Gemini supports at each schema node
_SUPPORTED_FIELDS = {"type", "description", "enum", "required", "items", "properties", "nullable"}

# Fields that must be removed (would cause Gemini to reject the schema)
_STRIP_FIELDS = {"$schema", "additionalProperties", "$ref", "$defs", "default", "title"}


def convert_property(schema: dict) -> dict:
    """Recursively convert a single JSON Schema node to Gemini format."""
    if not isinstance(schema, dict):
        return schema

    result = {}

    for key, value in schema.items():
        if key in _STRIP_FIELDS:
            continue

        if key == "type" and isinstance(value, list):
            # JSON Schema nullable: ["string", "null"] → extract the non-null type
            non_null = [t for t in value if t != "null"]
            result["type"] = non_null[0] if non_null else "STRING"
            result["nullable"] = "null" in value
        elif key == "properties":
            result["properties"] = {
                prop_name: convert_property(prop_schema)
                for prop_name, prop_schema in value.items()
            }
        elif key == "items":
            result["items"] = convert_property(value)
        elif key in _SUPPORTED_FIELDS:
            result[key] = value

    return result


def mcp_tool_to_gemini(tool) -> dict:
    """Convert an MCP Tool object to a Gemini function declaration dict.

    Args:
        tool: An MCP Tool object with .name, .description, and .inputSchema

    Returns:
        A dict suitable for inclusion in Gemini's function_declarations list.
    """
    decl = {
        "name": tool.name,
        "description": tool.description,
    }

    input_schema = tool.inputSchema
    if not input_schema:
        return decl

    # Build parameters from the input schema
    params = {}

    if "type" in input_schema:
        params["type"] = input_schema["type"]

    if "properties" in input_schema and input_schema["properties"]:
        params["properties"] = {
            prop_name: convert_property(prop_schema)
            for prop_name, prop_schema in input_schema["properties"].items()
        }

    if "required" in input_schema:
        params["required"] = input_schema["required"]

    if params:
        decl["parameters"] = params

    return decl
