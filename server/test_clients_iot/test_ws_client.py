import argparse
import asyncio
import json
import random
from datetime import datetime, timezone

import websockets


async def run_client(
    client_index: int,
    url: str,
    interval: float,
) -> None:
    client_name = f"client-{client_index}"
    device_id = f"device-{client_index}"
    switch_on = False

    async with websockets.connect(url) as ws:
        async def sender() -> None:
            seq = 0
            while True:
                payload = {
                    "id": device_id,
                    "client": client_name,
                    "seq": seq,
                    "status": "ok",
                    "payload": {
                        "temperature": random.randint(20, 35),
                        "switchOn": switch_on,
                    },
                }
                await ws.send(json.dumps(payload, ensure_ascii=False))
                print(f"[{client_name}] send: {payload}")
                seq += 1
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
                request_id = data.get("requestId")
                command_payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}

                if not isinstance(request_id, str) or not request_id:
                    # device-state-report should carry the same requestId from device-command.
                    continue

                if command == "toggle":
                    switch_on = not switch_on
                elif command == "set-switch":
                    if "switchOn" not in command_payload:
                        continue
                    switch_on = bool(command_payload.get("switchOn"))
                else:
                    continue

                report = {
                    "type": "device-state-report",
                    "id": device_id,
                    "client": client_name,
                    "status": "ok",
                    "source": "example-program",
                    "requestId": request_id,
                    "updatedAt": datetime.now(timezone.utc).isoformat(),
                    "payload": {
                        "switchOn": switch_on,
                    },
                }
                await ws.send(json.dumps(report, ensure_ascii=False))
                print(f"[{client_name}] report: {report}")

        await asyncio.gather(sender(), receiver())


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://localhost:8081")
    parser.add_argument("--clients", type=int, default=3)
    parser.add_argument("--interval", type=float, default=0.5)
    args = parser.parse_args()

    tasks = [
        run_client(i, args.url, args.interval)
        for i in range(args.clients)
    ]
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
