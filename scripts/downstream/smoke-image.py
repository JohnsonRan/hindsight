"""Run inside the built image: actual API, control plane and streamed retain/recall.

Only the client and provider-test harness are mounted from /source. The API must
come from the image, never from the checkout being validated.
"""

import asyncio
import inspect
import os
import subprocess
from pathlib import Path

import aiohttp
from hindsight_api.engine.providers.openai_compatible_llm import OpenAICompatibleLLM
from hindsight_client import Hindsight
from hindsight_system_tests import Stubs, start_stub_server, wait_until_settled
from hindsight_system_tests.payloads import consolidation, extracted, fact
from hindsight_system_tests.server import stub_environment


async def main() -> None:
    assert Path(inspect.getfile(OpenAICompatibleLLM)).is_relative_to("/app/api")
    stubs = Stubs()
    stubs.llm.on_step("extract_facts", contains="downstream image smoke").returns(
        extracted(fact("A downstream image smoke memory was retained"))
    )
    stubs.llm.on_step("consolidate").returns(consolidation())
    stub = start_stub_server(stubs)
    env = {
        **os.environ,
        **stub_environment(stub.url),
        "HINDSIGHT_API_LLM_STREAM": "true",
        "HINDSIGHT_API_DATABASE_URL": "pg0://downstream-image-smoke:15499",
        "HINDSIGHT_API_HOST": "0.0.0.0",
        "HINDSIGHT_API_PORT": "8888",
        "HINDSIGHT_ENABLE_API": "true",
        "HINDSIGHT_ENABLE_CP": "true",
    }
    process = subprocess.Popen(["/app/start-all.sh"], env=env)
    client = Hindsight(base_url="http://127.0.0.1:8888")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as http:
            for _ in range(300):
                if process.poll() is not None:
                    raise RuntimeError(f"Container services exited with {process.returncode}")
                try:
                    async with http.get("http://127.0.0.1:8888/health") as response:
                        response.raise_for_status()
                    async with http.get("http://127.0.0.1:9999/api/health") as response:
                        response.raise_for_status()
                        health = await response.json()
                        if health.get("dataplane", {}).get("status") == "connected":
                            break
                except (aiohttp.ClientError, TimeoutError):
                    pass
                await asyncio.sleep(1)
            else:
                raise RuntimeError("API and control plane did not become healthy")

        bank = "downstream-image-smoke"
        await client.aretain(bank_id=bank, content="This is a downstream image smoke story.")
        await wait_until_settled(client, bank)
        recalled = await client.arecall(bank_id=bank, query="downstream image smoke")
        assert [item.text for item in recalled.results] == ["A downstream image smoke memory was retained"]
        assert not stubs.llm.unmatched
        assert not stubs.rejected_requests
        print("Shipped API + control plane + streamed retain/recall passed", flush=True)
    finally:
        await client.aclose()
        process.terminate()
        try:
            process.wait(timeout=75)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        stub.stop()


if __name__ == "__main__":
    asyncio.run(main())
