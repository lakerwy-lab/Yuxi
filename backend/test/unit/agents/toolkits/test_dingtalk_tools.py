from yuxi.agents.toolkits.dingtalk import my_bookings


def test_my_bookings_schema_exposes_only_public_fields():
    """my_bookings 的公开 schema 不应包含内部 ToolRuntime。"""

    schema = my_bookings.args_schema.model_json_schema()
    properties = schema.get("properties", {})
    assert "runtime" not in properties
    assert "status" in properties
