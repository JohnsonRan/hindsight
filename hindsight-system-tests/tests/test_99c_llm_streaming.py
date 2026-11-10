"""A streamed extraction is validated, stored, and recallable after background work."""

from collections.abc import Iterator

import pytest

from hindsight_system_tests import start_hindsight_server
from hindsight_system_tests.payloads import consolidation, extracted, fact
from hindsight_system_tests.server import HindsightServer, StubServer

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def hindsight_server(stub_server: StubServer, tmp_path_factory: pytest.TempPathFactory) -> Iterator[HindsightServer]:
    # Streaming is server-level. A separate schema also keeps other servers' workers
    # from claiming this story's consolidation with their non-streaming configuration.
    server = start_hindsight_server(
        stub_url=stub_server.url,
        log_path=tmp_path_factory.mktemp("streaming-server") / "server.log",
        extra_env={
            "HINDSIGHT_API_LLM_STREAM": "true",
            "HINDSIGHT_API_DATABASE_SCHEMA": "systest_streaming",
        },
    )
    yield server
    server.stop()


async def test_streamed_retain_survives_consolidation(client, llm, bank_id, settled) -> None:
    llm.on_step("extract_facts", contains="streaming story").returns(
        extracted(fact("A streaming story was retained")),
    )
    llm.on_step("consolidate").returns(consolidation())

    await client.aretain(bank_id=bank_id, content="This is a streaming story.")
    await settled(bank_id)

    recalled = await client.arecall(bank_id=bank_id, query="streaming story")
    assert [result.text for result in recalled.results] == ["A streaming story was retained"]
    assert recalled.results[0].type == "world"
    assert recalled.results[0].tags == []
    assert recalled.results[0].metadata == {}
