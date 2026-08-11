from yuxi.agents.toolkits.dingtalk import my_bookings


def test_my_bookings_exposes_empty_public_schema():
    """无参数工具不应把内部 ToolRuntime 暴露给模型或前端。"""
    schema = my_bookings.args_schema.model_json_schema()

    assert schema["properties"] == {}
