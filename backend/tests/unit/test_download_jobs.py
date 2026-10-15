"""A model download outlives the page that asked for it, and asking again joins it."""

import asyncio

from app import api_desktop
from app.api_desktop import DownloadModelsInput


def test_a_second_request_joins_the_running_download(monkeypatch):
    starts = []

    async def fake(body, job):
        starts.append(body.chat_id)
        job["model"], job["percent"] = "chat", 40
        await asyncio.sleep(0.05)
        return {"status": "ok"}

    monkeypatch.setattr(api_desktop, "_download_models", fake)
    monkeypatch.setattr(api_desktop, "_downloads", {})

    async def main():
        body = DownloadModelsInput(chat_id="qwen35-9b-q4", include_multimodal=False)
        first = asyncio.create_task(api_desktop.download_models(body))
        await asyncio.sleep(0.01)
        running = await api_desktop.downloads_in_flight()
        second = await api_desktop.download_models(body)
        return running, await first, second, await api_desktop.downloads_in_flight()

    running, first, second, after = asyncio.run(main())
    assert starts == ["qwen35-9b-q4"]
    assert running == {"chat:qwen35-9b-q4": {"model": "chat", "percent": 40}}
    assert first == second == {"status": "ok"} and after == {}
