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
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OledState:
    device_id: str
    client_id: str
    rows: int
    chars_per_row: int
    line_height: int
    port: int
    address: int
    status: str = "ok"
    seq: int = 0
    source: str = "oled-device"
    last_command: str | None = None
    last_error: str | None = None
    _lines: list[str] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self._lines:
            self._lines = ["" for _ in range(self.rows)]

    def snapshot(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rows": self.rows,
            "charsPerRow": self.chars_per_row,
            "lineHeight": self.line_height,
            "i2cPort": self.port,
            "i2cAddress": f"0x{self.address:02X}",
            "sampledAt": _utc_now_iso(),
            "lines": list(self._lines),
        }
        if self.last_command is not None:
            payload["lastCommand"] = self.last_command
        if self.last_error is not None:
            payload["lastError"] = self.last_error
        return payload

    def next_send(self) -> dict[str, Any]:
        message = {
            "id": self.device_id,
            "client": self.client_id,
            "seq": self.seq,
            "status": self.status,
            "payload": self.snapshot(),
        }
        self.seq += 1
        return message

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
            "updatedAt": _utc_now_iso(),
            "payload": payload,
        }


class OledDisplay:
    def __init__(self, port: int, address: int, line_height: int):
        self.port = port
        self.address = address
        self.line_height = line_height
        interface = i2c(port=port, address=address)
        self.device = ssd1306(interface, rotate=0)

    def render(self, lines: list[str]) -> None:
        with canvas(self.device) as draw:
            for index, line in enumerate(lines):
                draw.text((0, index * self.line_height), line, fill=1)


def _normalize_lines(payload: dict[str, Any], rows: int, chars_per_row: int) -> tuple[list[str], str | None]:
    if "lines" in payload:
        raw_lines = payload.get("lines")
        if not isinstance(raw_lines, list):
            return [], "payload.lines must be an array of strings"
        if len(raw_lines) > rows:
            return [], f"payload.lines length must be <= {rows}"

        result: list[str] = []
        for item in raw_lines:
            if not isinstance(item, str):
                return [], "payload.lines items must be strings"
            result.append(item[:chars_per_row])

        while len(result) < rows:
            result.append("")
        return result, None

    result = []
    for i in range(rows):
        text = payload.get(f"line{i + 1}", "")
        if not isinstance(text, str):
            return [], f"payload.line{i + 1} must be a string"
        result.append(text[:chars_per_row])
    return result, None


async def _apply_lines(state: OledState, display: OledDisplay, lines: list[str]) -> None:
    async with state._lock:
        state._lines = lines
        display.render(lines)


async def _handle_command(state: OledState, display: OledDisplay, message: dict[str, Any]) -> dict[str, Any] | None:
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

    command_name = command.strip().lower()
    if command_name not in {"set-text", "show-text", "render-text"}:
        return {
            **state.report(request_id, "refused", command_name),
            "payload": {**state.snapshot(), "reason": f"unsupported command: {command_name}"},
        }

    raw_payload = message.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    lines, error = _normalize_lines(payload, state.rows, state.chars_per_row)
    if error is not None:
        return {
            **state.report(request_id, "refused", command_name),
            "payload": {**state.snapshot(), "reason": error},
        }

    try:
        await _apply_lines(state, display, lines)
        state.status = "ok"
        state.last_command = command_name
        state.last_error = None
        return state.report(request_id, "ok", command_name)
    except Exception as exc:
        state.status = "error"
        state.last_command = command_name
        state.last_error = str(exc)
        return {
            **state.report(request_id, "failed", command_name),
            "payload": {**state.snapshot(), "error": str(exc)},
        }


async def _sender(ws: Any, state: OledState, interval: float) -> None:
    while True:
        await ws.send(json.dumps(state.next_send(), ensure_ascii=False))
        await asyncio.sleep(interval)


async def _receiver(ws: Any, state: OledState, display: OledDisplay) -> None:
    while True:
        raw = await ws.recv()
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if not isinstance(message, dict):
            continue

        response = await _handle_command(state, display, message)
        if response is not None:
            await ws.send(json.dumps(response, ensure_ascii=False))


async def run_client(url: str, state: OledState, display: OledDisplay, interval: float, reconnect_delay: float) -> None:
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                sender_task = asyncio.create_task(_sender(ws, state, interval))
                receiver_task = asyncio.create_task(_receiver(ws, state, display))
                done, pending = await asyncio.wait({sender_task, receiver_task}, return_when=asyncio.FIRST_EXCEPTION)

                for task in pending:
                    task.cancel()
                for task in done:
                    task.result()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state.status = "error"
            state.last_error = str(exc)
            await asyncio.sleep(reconnect_delay)
        else:
            await asyncio.sleep(reconnect_delay)


def _parse_i2c_address(raw: str) -> int:
    return int(raw, 0)


async def main() -> None:
    parser = argparse.ArgumentParser(description="OLED text device client for the hyperautomation WS protocol")
    parser.add_argument("--url", default=os.getenv("WS_URL", "ws://localhost:8081"))
    parser.add_argument("--device-id", default=os.getenv("OLED_DEVICE_ID", "oled-0"))
    parser.add_argument("--client-id", default=os.getenv("OLED_CLIENT_ID", "oled-0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OLED_I2C_PORT", "1")))
    parser.add_argument("--address", type=_parse_i2c_address, default=_parse_i2c_address(os.getenv("OLED_I2C_ADDRESS", "0x3C")))
    parser.add_argument("--rows", type=int, default=int(os.getenv("OLED_ROWS", "4")))
    parser.add_argument("--chars-per-row", type=int, default=int(os.getenv("OLED_CHARS_PER_ROW", "20")))
    parser.add_argument("--line-height", type=int, default=int(os.getenv("OLED_LINE_HEIGHT", "16")))
    parser.add_argument("--interval", type=float, default=float(os.getenv("OLED_REPORT_INTERVAL", "2.0")))
    parser.add_argument("--reconnect-delay", type=float, default=float(os.getenv("OLED_RECONNECT_DELAY", "3.0")))
    args = parser.parse_args()

    rows = max(1, args.rows)
    chars_per_row = max(1, args.chars_per_row)
    line_height = max(8, args.line_height)

    display = OledDisplay(port=args.port, address=args.address, line_height=line_height)
    state = OledState(
        device_id=args.device_id,
        client_id=args.client_id,
        rows=rows,
        chars_per_row=chars_per_row,
        line_height=line_height,
        port=args.port,
        address=args.address,
    )

    await _apply_lines(state, display, ["" for _ in range(rows)])

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _request_stop() -> None:
        stop_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, _request_stop)

    client_task = asyncio.create_task(run_client(args.url, state, display, args.interval, args.reconnect_delay))
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


if __name__ == "__main__":
    asyncio.run(main())
