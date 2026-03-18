function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const serverBase = process.env.WS_SERVER_URL || 'http://127.0.0.1:8081';
const targetId = 'device-0';
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
    }),
  });

  const result = await response.json();

  if (!response.ok) {
    throw new Error(result.message || `HTTP ${response.status}`);
  }

  console.log(
    `alpha tick ${tick}: server toggled ${targetId}, currentSwitchOn=${result.currentSwitchOn}`
  );
}

console.log('alpha control script started');

while (running) {
  await sleep(5000);

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
