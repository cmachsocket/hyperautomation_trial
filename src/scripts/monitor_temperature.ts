function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const serverBase = process.env.WS_SERVER_URL || 'http://127.0.0.1:8081';
const targetId = process.env.TARGET_DEVICE_ID || 'device-0';
const tickIntervalMs = Number(process.env.MONITOR_INTERVAL_MS || 5000);
let running = true;
let tick = 0;

process.on('SIGTERM', () => {
  running = false;
});

async function getDeviceTemperature() {
  const response = await fetch(`${serverBase}/api/device/${targetId}`, {
    method: 'GET',
    headers: {
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }

  const result = await response.json();
  return result?.payload?.temperature ?? null;
}

if (!Number.isFinite(tickIntervalMs) || tickIntervalMs <= 0) {
  throw new Error(`Invalid MONITOR_INTERVAL_MS: ${process.env.MONITOR_INTERVAL_MS}`);
}

console.log(`temperature monitor script started, targetId=${targetId}, intervalMs=${tickIntervalMs}`);

while (running) {
  await sleep(tickIntervalMs);

  if (!running) {
    break;
  }

  tick += 1;

  try {
    const temperature = await getDeviceTemperature();
    console.log(`monitor tick ${tick}: ${targetId} temperature = ${temperature}°C`);
  } catch (error) {
    console.error(`monitor tick ${tick}: ${error.message}`);
  }
}

console.log('temperature monitor script stopped');
