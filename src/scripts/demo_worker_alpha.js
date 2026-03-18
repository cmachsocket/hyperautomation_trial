function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const serverBase = process.env.WS_SERVER_URL || 'http://127.0.0.1:8081';
const targetId = process.env.TARGET_DEVICE_ID || 'device-0';
const tickIntervalMs = Number(process.env.ALPHA_INTERVAL_MS || 5000);
let running = true;
let tick = 0;

process.on('SIGTERM', () => {
  running = false;
});

async function toggleByServer() {
  const response = await fetch(`${serverBase}/api/device/command`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      id: targetId,
      command: 'toggle',
      source: 'demo-worker-alpha',
      payload: {},
    }),
  });

  const result = await response.json();

  if (!response.ok) {
    throw new Error(result.message || `HTTP ${response.status}: ${JSON.stringify(result)}`);
  }

  const switchOn = result?.updated?.payload?.switchOn;

  console.log(
    `alpha tick ${tick}: server toggled ${targetId}, switchOn=${switchOn}, response=${JSON.stringify(result)}`
  );
}

if (!Number.isFinite(tickIntervalMs) || tickIntervalMs <= 0) {
  throw new Error(`Invalid ALPHA_INTERVAL_MS: ${process.env.ALPHA_INTERVAL_MS}`);
}

console.log(`alpha control script started, targetId=${targetId}, intervalMs=${tickIntervalMs}`);

while (running) {
  await sleep(tickIntervalMs);

  if (!running) {
    break;
  }

  tick += 1;

  try {
    await toggleByServer();
  } catch (error) {
    console.error(`alpha tick ${tick}: ${error.message}`);
  }
}

console.log('alpha control script stopped');
