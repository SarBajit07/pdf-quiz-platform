import httpx
import pytest

from app.llm import LLMResponseError, OpenAICompatibleClient, Provider


@pytest.mark.parametrize("primary_choices", [[], [{"message": {"content": "{}"}}]])
async def test_invalid_responses_trigger_provider_fallback(
    monkeypatch: pytest.MonkeyPatch, primary_choices: list[dict],
) -> None:
    requests = []

    def reply(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.host)
        if request.url.host == "primary.test":
            return httpx.Response(200, json={"choices": primary_choices})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})

    async def skip_wait(_seconds: float) -> None:
        pass

    async_client = httpx.AsyncClient

    def make_http_client(**kwargs) -> httpx.AsyncClient:
        return async_client(transport=httpx.MockTransport(reply), **kwargs)

    monkeypatch.setattr("app.llm.asyncio.sleep", skip_wait)
    monkeypatch.setattr("app.llm.httpx.AsyncClient", make_http_client)
    client = OpenAICompatibleClient([
        Provider("primary", "https://primary.test", "", "primary-model"),
        Provider("fallback", "https://fallback.test", "", "fallback-model"),
    ])

    def validate(data: object) -> bool:
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise LLMResponseError("Expected a successful response")
        return True

    assert await client.complete_json("system", "prompt", validate=validate) == (
        True, "fallback-model",
    )
    assert requests == ["primary.test", "primary.test", "fallback.test"]
