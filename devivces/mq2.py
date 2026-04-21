from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import RPi.GPIO as GPIO  # type: ignore[import-not-found]
import websockets


def _utc_now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


@dataclass
class MQ2State:
	device_id: str
	client_id: str
	pin: int
	status: str = "ok"
	seq: int = 0
	last_error: str | None = None

	def read_payload(self) -> dict[str, Any]:
		raw_value = int(GPIO.input(self.pin))
		gas_detected = raw_value == 0
		payload: dict[str, Any] = {
			"gasDetected": gas_detected,
			"rawValue": raw_value,
			"pin": self.pin,
			"sampledAt": _utc_now_iso(),
		}
		if self.last_error is not None:
			payload["lastError"] = self.last_error
		return payload

	def next_send(self) -> dict[str, Any]:
		message = {
			"id": self.device_id,
			"client": self.client_id,
			"seq": self.seq,
			"status": self.status,
			"payload": self.read_payload(),
		}
		self.seq += 1
		return message


async def _sender(ws: Any, state: MQ2State, interval: float) -> None:
	while True:
		try:
			message = state.next_send()
			state.status = "ok"
			state.last_error = None
		except Exception as exc:
			state.status = "error"
			state.last_error = str(exc)
			message = {
				"id": state.device_id,
				"client": state.client_id,
				"seq": state.seq,
				"status": state.status,
				"payload": {
					"pin": state.pin,
					"sampledAt": _utc_now_iso(),
					"lastError": state.last_error,
				},
			}
			state.seq += 1

		await ws.send(json.dumps(message, ensure_ascii=False))
		await asyncio.sleep(interval)


async def run_client(url: str, state: MQ2State, interval: float, reconnect_delay: float) -> None:
	while True:
		try:
			async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
				await _sender(ws, state, interval)
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			state.status = "error"
			state.last_error = str(exc)
			await asyncio.sleep(reconnect_delay)


async def main() -> None:
	parser = argparse.ArgumentParser(description="MQ2 upload-only client for the hyperautomation WS protocol")
	parser.add_argument("--url", default=os.getenv("WS_URL", "ws://localhost:8081"))
	parser.add_argument("--device-id", default=os.getenv("MQ2_DEVICE_ID", "mq2-0"))
	parser.add_argument("--client-id", default=os.getenv("MQ2_CLIENT_ID", "mq2-0"))
	parser.add_argument("--pin", type=int, default=int(os.getenv("MQ2_PIN", "17")))
	parser.add_argument("--interval", type=float, default=float(os.getenv("MQ2_REPORT_INTERVAL", "1.0")))
	parser.add_argument("--reconnect-delay", type=float, default=float(os.getenv("MQ2_RECONNECT_DELAY", "3.0")))
	args = parser.parse_args()

	GPIO.setmode(GPIO.BCM)
	GPIO.setup(args.pin, GPIO.IN)

	state = MQ2State(
		device_id=args.device_id,
		client_id=args.client_id,
		pin=args.pin,
	)

	loop = asyncio.get_running_loop()
	stop_event = asyncio.Event()

	def _request_stop() -> None:
		stop_event.set()

	for sig_name in ("SIGINT", "SIGTERM"):
		sig = getattr(signal, sig_name, None)
		if sig is not None:
			with suppress(NotImplementedError):
				loop.add_signal_handler(sig, _request_stop)

	client_task = asyncio.create_task(run_client(args.url, state, args.interval, args.reconnect_delay))
	stopper_task = asyncio.create_task(stop_event.wait())

	try:
		done, pending = await asyncio.wait({client_task, stopper_task}, return_when=asyncio.FIRST_COMPLETED)
		for task in pending:
			task.cancel()
		for task in done:
			task.result()
	finally:
		client_task.cancel()
		with suppress(asyncio.CancelledError):
			await client_task
		GPIO.cleanup()


if __name__ == "__main__":
	asyncio.run(main())
