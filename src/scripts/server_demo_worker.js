// Server Demo Worker - 连接到server的演示脚本
// 环境变量: SERVER_URL (默认 http://127.0.0.1:8081)
//           TARGET_DEVICE_ID (默认 device-0)
//           INTERVAL_MS (默认 5000ms)

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const serverBase = process.env.SERVER_URL || 'http://127.0.0.1:8081';
const targetId = process.env.TARGET_DEVICE_ID || 'device-0';
const intervalMs = Number(process.env.INTERVAL_MS || 5000);

let running = true;
let requestCount = 0;

// 优雅退出
process.on('SIGTERM', () => {
  console.log('\n收到退出信号，正在停止...');
  running = false;
});

process.on('SIGINT', () => {
  console.log('\n收到中断信号，正在停止...');
  running = false;
});

// 向服务器发送命令
async function sendCommandToServer() {
  const endpoint = `${serverBase}/api/device/command`;
  
  const commandData = {
    id: targetId,
    command: 'demo_action',
    source: 'server-demo-worker',
    payload: {
      timestamp: Date.now(),
      sequence: ++requestCount,
      action: Math.random() > 0.5 ? 'enable' : 'disable',
    }
  };

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(commandData),
    });

    const result = await response.json();
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${result.message || JSON.stringify(result)}`);
    }

    console.log(
      `[${new Date().toISOString()}] ✅ 请求成功 | ` +
      `设备: ${targetId} | 动作: ${commandData.payload.action} | ` +
      `序列号: ${requestCount}`
    );
    
    return result;
  } catch (error) {
    console.error(
      `[${new Date().toISOString()}] ❌ 请求失败 | ` +
      `错误: ${error.message}`
    );
    return null;
  }
}

// 验证服务器健康状态
async function checkServerHealth() {
  const healthUrl = `${serverBase}/api/health`;
  
  try {
    const response = await fetch(healthUrl, {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (response.ok) {
      const health = await response.json();
      console.log(`🟢 服务器健康检查通过: ${JSON.stringify(health)}`);
      return true;
    } else {
      console.warn(`⚠️ 服务器健康检查异常: HTTP ${response.status}`);
      return false;
    }
  } catch (error) {
    console.error(`🔴 无法连接到服务器: ${error.message}`);
    return false;
  }
}

console.log(`\n🚀 开始启动服务器Demo Worker...`);
console.log(`📡 服务器地址: ${serverBase}`);
console.log(`🎯 目标设备: ${targetId}`);
console.log(`⏱️ 执行间隔: ${intervalMs}ms\n`);

// 先检查服务器是否可访问
const serverAvailable = await checkServerHealth();
if (!serverAvailable) {
  console.log('⚠️  服务器暂时不可用，将在下次循环重试...\n');
}

while (running) {
  await sleep(intervalMs);

  if (!running) {
    break;
  }

  try {
    const result = await sendCommandToServer();
    
    // 模拟根据响应调整后续行为
    if (result && result.updated) {
      console.log(`💡 服务器返回更新信息: ${JSON.stringify(result.updated)}`);
    }
    
  } catch (error) {
    console.error(`❌ 循环内错误: ${error.message}`);
  }
}

console.log('\n👋 Server Demo Worker 已停止');
