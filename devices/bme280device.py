from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import signal
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_enabled(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _debug_log(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[bme280-debug] {message}")


def _import_bme280_module() -> Any:
    # 临时移除当前目录路径，避免优先导入本地同名模块。
    current_dir = str(Path(__file__).resolve().parent)
    removed = False
    if current_dir in sys.path:
        sys.path.remove(current_dir)
        removed = True
    try:
        module = importlib.import_module("bme280")
    finally:
        if removed:
            sys.path.insert(0, current_dir)
    return module


class BME280Sampler:
    def __init__(self, port: int, address: int, debug: bool = False) -> None:
        self.port = port
        self.address = address
        self.debug = debug
        self._smbus2: Any | None = None
        self._bme280: Any | None = None
        self._bus: Any | None = None
        self._calibration: Any | None = None

    def _ensure_ready(self) -> None:
        if self._smbus2 is None:
            self._smbus2 = importlib.import_module("smbus2")
            _debug_log(self.debug, "smbus2 module loaded")
        if self._bme280 is None:
            try:
                self._bme280 = _import_bme280_module()
                module_path = getattr(self._bme280, "__file__", "<unknown>")
                _debug_log(self.debug, f"bme280 module loaded from {module_path}")
            except Exception as exc:
                _debug_log(self.debug, f"failed to load bme280 module: {exc}")
                raise

        assert self._smbus2 is not None
        assert self._bme280 is not None

        if self._bus is None:
            self._bus = self._smbus2.SMBus(self.port)
            _debug_log(self.debug, f"i2c bus opened on port={self.port}")

        if self._calibration is None:
            self._calibration = self._bme280.load_calibration_params(self._bus, self.address)
            _debug_log(self.debug, f"calibration loaded for address=0x{self.address:02X}")

    def read(self) -> dict[str, Any]:
        self._ensure_ready()
        assert self._bme280 is not None
        assert self._bus is not None
        assert self._calibration is not None

        data = self._bme280.sample(self._bus, self.address, self._calibration)
        temperature = round(float(data.temperature), 2)
        pressure = round(float(data.pressure), 2)
        humidity = round(float(data.humidity), 2)
        _debug_log(
            self.debug,
            (
                "sample ok "
                f"temperatureC={temperature}, pressureHpa={pressure}, humidityPct={humidity}"
            ),
        )
        return {
            "temperatureC": temperature,
            "pressureHpa": pressure,
            "humidityPct": humidity,
            "i2cPort": self.port,
            "i2cAddress": f"0x{self.address:02X}",
            "sampledAt": _utc_now_iso(),
        }

    def reset(self) -> None:
        if self._bus is not None:
            with suppress(Exception):
                self._bus.close()
            _debug_log(self.debug, "i2c bus closed")
        self._bus = None
        self._calibration = None


@dataclass
class BME280State:
    device_id: str
    client_id: str
    sampler: BME280Sampler
    status: str = "ok"
    seq: int = 0
    last_error: str | None = None

    def read_payload(self) -> dict[str, Any]:
        payload = self.sampler.read()
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


async def _sender(ws: Any, state: BME280State, interval: float) -> None:
    while True:
        try:
            message = state.next_send()
            state.status = "ok"
            state.last_error = None
        except Exception as exc:
            state.status = "error"
            state.last_error = str(exc)
            _debug_log(state.sampler.debug, f"sample failed: {exc}")
            state.sampler.reset()
            message = {
                "id": state.device_id,
                "client": state.client_id,
                "seq": state.seq,
                "status": state.status,
                "payload": {
                    "sampledAt": _utc_now_iso(),
                    "i2cPort": state.sampler.port,
                    "i2cAddress": f"0x{state.sampler.address:02X}",
                    "lastError": state.last_error,
                },
            }
            state.seq += 1

        await ws.send(json.dumps(message, ensure_ascii=False))
        await asyncio.sleep(interval)


async def run_client(url: str, state: BME280State, interval: float, reconnect_delay: float) -> None:
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


def _parse_i2c_address(raw: str) -> int:
    return int(raw, 0)


async def main() -> None:
    parser = argparse.ArgumentParser(description="BME280 upload-only client for the hyperautomation WS protocol")
    parser.add_argument("--url", default=os.getenv("WS_URL", "ws://localhost:8081"))
    parser.add_argument("--device-id", default=os.getenv("BME280_DEVICE_ID", "bme280-0"))
    parser.add_argument("--client-id", default=os.getenv("BME280_CLIENT_ID", "bme280-0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("BME280_I2C_PORT", "1")))
    parser.add_argument("--address", type=_parse_i2c_address, default=_parse_i2c_address(os.getenv("BME280_I2C_ADDRESS", "0x76")))
    parser.add_argument("--interval", type=float, default=float(os.getenv("BME280_REPORT_INTERVAL", "2.0")))
    parser.add_argument("--reconnect-delay", type=float, default=float(os.getenv("BME280_RECONNECT_DELAY", "3.0")))
    parser.add_argument(
        "--debug",
        dest="debug",
        action="store_true",
        default=_is_enabled(os.getenv("BME280_DEBUG", "1")),
    )
    parser.add_argument("--no-debug", dest="debug", action="store_false")
    args = parser.parse_args()

    state = BME280State(
        device_id=args.device_id,
        client_id=args.client_id,
        sampler=BME280Sampler(port=args.port, address=args.address, debug=args.debug),
    )

    _debug_log(
        args.debug,
        (
            "client starting "
            f"url={args.url}, device_id={args.device_id}, port={args.port}, address=0x{args.address:02X}"
        ),
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
        state.sampler.reset()


if __name__ == "__main__":
    asyncio.run(main())
