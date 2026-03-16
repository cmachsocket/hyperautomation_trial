import argparse
import asyncio
import json
import random
from contextlib import suppress

import websockets


async def run_client(
    client_index: int,
    url: str,
    messages: int,
    interval: float,
    listen_seconds: float,
) -> None:
    client_name = f"client-{client_index}"
    device_id = f"device-{client_index % 2}"
    switch_on = False

    async with websockets.connect(url) as ws:
        async def sender() -> None:
            for seq in range(messages):
                payload = {
                    "id": device_id,
                    "client": client_name,
                    "seq": seq,
                    "temperature": random.randint(20, 35),
                    "status": "ok",
                    "switchOn": switch_on,
                }
                await ws.send(json.dumps(payload, ensure_ascii=False))
                print(f"[{client_name}] send: {payload}")
                await asyncio.sleep(interval)

        async def receiver() -> None:
            nonlocal switch_on

            while True:
                message = await ws.recv()
                print(f"[{client_name}] recv: {message}")

                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue

                if data.get("type") != "device-command" or data.get("id") != device_id:
                    continue

                command = data.get("command")
                if command == "toggle":
                    switch_on = not switch_on
                elif command == "set-switch":
                    switch_on = bool(data.get("switchOn"))
                else:
                    continue

                report = {
                    "type": "device-state-report",
                    "id": device_id,
                    "client": client_name,
                    "switchOn": switch_on,
                    "status": "ok",
                    "source": "example-program",
                    "requestId": data.get("requestId"),
                }
                await ws.send(json.dumps(report, ensure_ascii=False))
                print(f"[{client_name}] report: {report}")

        receiver_task = asyncio.create_task(receiver())

        await sender()
        await asyncio.sleep(listen_seconds)

        receiver_task.cancel()
        with suppress(asyncio.CancelledError):
            await receiver_task


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://localhost:8081")
    parser.add_argument("--clients", type=int, default=3)
    parser.add_argument("--messages", type=int, default=3)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--listen-seconds", type=float, default=5.0)
    args = parser.parse_args()

    tasks = [
        run_client(i, args.url, args.messages, args.interval, args.listen_seconds)
        for i in range(args.clients)
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
