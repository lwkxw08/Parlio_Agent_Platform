from parlio_api.settings import Settings


def test_llm_key_auto_uses_openai_only_when_key_present() -> None:
    assert Settings(openai_api_key=None).llm_key is None
    assert Settings(openai_api_key="sk-test").llm_key == "sk-test"
    assert Settings(openai_api_key="sk-test", postcall_analyser="heuristic").llm_key is None
    assert Settings(openai_api_key="sk-test", postcall_analyser="openai").llm_key == "sk-test"
