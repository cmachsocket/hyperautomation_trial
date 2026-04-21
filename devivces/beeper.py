from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import websockets
import RPi.GPIO as GPIO  # type: ignore[import-not-found]


@dataclass
class BeeperState:
	device_id: str
	client_id: str
	pin: int
	active_high: bool = True
	status: str = "ok"
	beeper_on: bool = False
	seq: int = 0
	source: str = "beeper-device"
	last_command: str | None = None
	last_error: str | None = None
	_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

	def snapshot(self) -> dict[str, Any]:
		payload: dict[str, Any] = {
			"beeperOn": self.beeper_on,
			"pin": self.pin,
		}
		if self.last_command is not None:
			payload["lastCommand"] = self.last_command
		if self.last_error is not None:
			payload["lastError"] = self.last_error
		return payload

	def next_send(self) -> dict[str, Any]:
		payload = {
			"id": self.device_id,
			"client": self.client_id,
			"seq": self.seq,
			"status": self.status,
			"payload": self.snapshot(),
		}
		self.seq += 1
		return payload

	def report(self, request_id: str, status: str, command: str) -> dict[str, Any]:
		payload = self.snapshot()
		payload["command"] = command
		return {
			"type": "device-state-report",
			"id": self.device_id,
			"client": self.client_id,
			"status": status,
			"source": self.source,
			"requestId": request_id,
			"updatedAt": datetime.now(timezone.utc).isoformat(),
			"payload": payload,
		}


def _utc_now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def _gpio_value(active_high: bool, beeper_on: bool) -> bool:
	return beeper_on if active_high else not beeper_on


async def _apply_gpio_state(state: BeeperState, beeper_on: bool) -> None:
	async with state._lock:
		state.beeper_on = beeper_on
		GPIO.output(state.pin, _gpio_value(state.active_high, beeper_on))


def _extract_bool(payload: dict[str, Any], keys: tuple[str, ...]) -> bool | None:
	for key in keys:
		if key in payload:
			return bool(payload[key])
	return None


def _extract_duration_ms(payload: dict[str, Any], default_ms: int) -> int:
	value = payload.get("durationMs")
	if isinstance(value, (int, float)):
		return max(0, int(value))
	return max(0, default_ms)


async def _handle_command(state: BeeperState, message: dict[str, Any]) -> dict[str, Any] | None:
	if message.get("type") != "device-command":
		return None

	if message.get("id") != state.device_id:
		return None

	request_id = message.get("requestId")
	if not isinstance(request_id, str) or not request_id:
		return {
			"type": "device-state-report",
			"id": state.device_id,
			"client": state.client_id,
			"status": "refused",
			"source": state.source,
			"requestId": "",
			"updatedAt": _utc_now_iso(),
			"payload": {**state.snapshot(), "reason": "missing requestId"},
		}

	command = message.get("command")
	if not isinstance(command, str) or not command.strip():
		return state.report(request_id, "refused", "unknown")

	raw_payload = message.get("payload")
	payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
	command_name = command.strip().lower()

	try:
		if command_name in {"toggle", "set-switch", "set-beeper", "set-buzzer", "set-state"}:
			if command_name == "toggle":
				async with state._lock:
					next_state = not state.beeper_on
				await _apply_gpio_state(state, next_state)
			else:
				desired = _extract_bool(payload, ("beeperOn", "switchOn", "on", "enabled", "value"))
				if desired is None:
					return {
						**state.report(request_id, "refused", command_name),
						"payload": {**state.snapshot(), "reason": "missing beeperOn/switchOn boolean"},
					}
				await _apply_gpio_state(state, desired)

		elif command_name in {"beep", "pulse", "buzz"}:
			duration_ms = _extract_duration_ms(payload, default_ms=300)
			await _apply_gpio_state(state, True)
			if duration_ms > 0:
				await asyncio.sleep(duration_ms / 1000)
			await _apply_gpio_state(state, False)

		elif command_name in {"on", "open", "start"}:
			await _apply_gpio_state(state, True)

		elif command_name in {"off", "close", "stop"}:
			await _apply_gpio_state(state, False)

		else:
			return {
				**state.report(request_id, "refused", command_name),
				"payload": {**state.snapshot(), "reason": f"unsupported command: {command_name}"},
			}

		async with state._lock:
			state.status = "ok"
			state.last_command = command_name
			state.last_error = None
		return state.report(request_id, "ok", command_name)
	except Exception as exc:
		async with state._lock:
			state.status = "error"
			state.last_command = command_name
			state.last_error = str(exc)
		return {
			**state.report(request_id, "failed", command_name),
			"payload": {**state.snapshot(), "error": str(exc)},
		}


async def _sender(ws: Any, state: BeeperState, interval: float) -> None:
	while True:
		await ws.send(json.dumps(state.next_send(), ensure_ascii=False))
		await asyncio.sleep(interval)


async def _receiver(ws: Any, state: BeeperState) -> None:
	while True:
		raw = await ws.recv()
		try:
			message = json.loads(raw)
		except json.JSONDecodeError:
			continue

		if not isinstance(message, dict):
			continue

		response = await _handle_command(state, message)
		if response is not None:
			await ws.send(json.dumps(response, ensure_ascii=False))


async def run_client(url: str, state: BeeperState, interval: float, reconnect_delay: float) -> None:
	while True:
		try:
			async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
				await _apply_gpio_state(state, False)
				sender_task = asyncio.create_task(_sender(ws, state, interval))
				receiver_task = asyncio.create_task(_receiver(ws, state))
				done, pending = await asyncio.wait(
					{sender_task, receiver_task},
					return_when=asyncio.FIRST_EXCEPTION,
				)

				for task in pending:
					task.cancel()
				for task in done:
					task.result()
		except asyncio.CancelledError:
			raise
		except Exception as exc:
			async with state._lock:
				state.status = "error"
				state.last_error = str(exc)
			await asyncio.sleep(reconnect_delay)
		else:
			await asyncio.sleep(reconnect_delay)


async def main() -> None:
	parser = argparse.ArgumentParser(description="Beeper device client for the hyperautomation WS protocol")
	parser.add_argument("--url", default=os.getenv("WS_URL", "ws://localhost:8081"))
	parser.add_argument("--device-id", default=os.getenv("BEEPER_DEVICE_ID", "beeper-0"))
	parser.add_argument("--client-id", default=os.getenv("BEEPER_CLIENT_ID", "beeper-0"))
	parser.add_argument("--pin", type=int, default=int(os.getenv("BEEPER_PIN", "10")))
	parser.add_argument("--interval", type=float, default=float(os.getenv("BEEPER_REPORT_INTERVAL", "2.0")))
	parser.add_argument("--reconnect-delay", type=float, default=float(os.getenv("BEEPER_RECONNECT_DELAY", "3.0")))
	parser.add_argument("--active-high", action=argparse.BooleanOptionalAction, default=True)
	args = parser.parse_args()

	GPIO.setmode(GPIO.BCM)
	GPIO.setup(args.pin, GPIO.OUT)

	state = BeeperState(
		device_id=args.device_id,
		client_id=args.client_id,
		pin=args.pin,
		active_high=args.active_high,
		source=os.getenv("BEEPER_SOURCE", "beeper-device"),
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
		GPIO.output(args.pin, _gpio_value(args.active_high, False))
		GPIO.cleanup()


if __name__ == "__main__":
	asyncio.run(main())