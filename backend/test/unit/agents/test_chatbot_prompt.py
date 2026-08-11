from types import SimpleNamespace

from yuxi.agents.buildin.chatbot.prompt import PROMPT, build_prompt_with_context


def test_chatbot_prompt_does_not_duplicate_html_preview_skill_instructions():
    assert "html:preview" not in PROMPT


def test_chatbot_prompt_includes_full_shanghai_timestamp():
    prompt = build_prompt_with_context(SimpleNamespace(system_prompt=""))

    assert "Asia/Shanghai" in prompt
    assert "+08:00" in prompt
