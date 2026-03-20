<template>
  <div class="device-monitor-container">
    <div class="header">
      <h3>🌡️ Device Monitor: {{ deviceId }}</h3>
      <span :class="['status-badge', connectionStatus]">
        {{ connectionText }}
      </span>
    </div>

    <div class="temperature-display">
      <div class="temperature-circle">
        <svg viewBox="0 0 36 36" class="temp-svg">
          <path class="temp-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
          <path 
            v-if="temperature !== null"
            :class="['temp-value', tempColorClass]" 
            :d="tempArc"
            :style="tempDashStyle"
          />
        </svg>
        <div class="temp-value-text">
          {{ temperature !== null ? temperature : '--' }}°C
        </div>
      </div>
    </div>

    <div class="control-panel">
      <div class="info-row">
        <span class="label">Status:</span>
        <span :class="['status-value', status ? 'ok' : 'error']">{{ status ? 'OK' : 'Error' }}</span>
      </div>
      
      <div class="info-row">
        <span class="label">Client ID:</span>
        <span>{{ clientId }}</span>
      </div>

      <div class="info-row">
        <span class="label">Last Update:</span>
        <span>{{ lastUpdateTime }}</span>
      </div>
      
      <div class="info-row">
        <span class="label">Temperature (raw):</span>
        <span>{{ rawTemperature }}</span>
      </div>
    </div>

    <div class="switch-control">
      <div class="switch-label">
        <span>Switch State:</span>
        <span :class="['switch-state', isOn]">{{ isOn ? 'ON' : 'OFF' }}</span>
      </div>
      <button 
        @click="toggleSwitch" 
        :disabled="!connected || loading"
        class="toggle-btn"
        :class="{ active: isOn }"
      >
        <span class="btn-text">{{ loading ? 'Processing...' : (isOn ? 'Turn Off' : 'Turn On') }}</span>
        <span class="btn-icon">{{ loading ? '⏳' : (isOn ? '❌' : '✅') }}</span>
      </button>
    </div>

    <div class="command-history" v-if="commandHistory.length > 0">
      <h4>Recent Commands</h4>
      <ul class="history-list">
        <li v-for="(cmd, index) in commandHistory.slice(-3).reverse()" :key="index">
          {{ cmd.timestamp }} - {{ cmd.command }} ({{ cmd.status }})
        </li>
      </ul>
    </div>

    <div class="debug-info" v-if="debugMode">
      <h4>Debug Information</h4>
      <pre>{{ debugInfo }}</pre>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted, onUnmounted } from 'vue';

const apiBase = (import.meta.env.VITE_API_BASE_URL || '').trim();
const wsBase = (import.meta.env.VITE_WS_URL || '').trim();

export default {
  name: 'DeviceMonitor',
  
  props: {
    deviceId: {
      type: String,
      default: 'device-0'
    },
    clientId: {
      type: String,
      default: 'client-0'
    },
    wsUrl: {
      type: String,
      default: wsBase || 'ws://localhost:8081'
    },
    apiUrl: {
      type: String,
      default: apiBase || 'http://localhost:8081'
    },
    debugMode: {
      type: Boolean,
      default: false
    }
  },

  setup(props) {
    // Connection status: 'connecting' | 'connected' | 'disconnected'
    const connectionStatus = ref('disconnected');
    const temperature = ref(null);
    const rawTemperature = ref('--');
    const isOn = ref(false);
    const status = ref('ok');
    const lastUpdateTime = ref('--');
    const commandHistory = ref([]);
    const loading = ref(false);
    
    // Computed for template usage
    const connected = computed(() => connectionStatus.value === 'connected');
    
    const connectionText = computed(() => {
      switch (connectionStatus.value) {
        case 'connecting': return '○ Connecting...';
        case 'connected': return '● Connected';
        case 'disconnected': return '○ Disconnected';
        default: return '○ Unknown';
      }
    });

    let ws = null;
    let reconnectTimeout = null;
    let pingInterval = null;
    const messageLog = ref([]);

    const tempColorClass = computed(() => {
      if (temperature.value === null) return '';
      if (temperature.value < 20) return 'temp-cold';
      if (temperature.value > 35) return 'temp-hot';
      return 'temp-warm';
    });

    // SVG path for the full circle (used as base)
    const tempArc = 'M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831';

    // Calculate the dash array offset based on temperature
    // Full circumference is 100 (stroke-dasharray reference)
    // We map temperature range 10-50°C to 0-100%
    const tempDashStyle = computed(() => {
      if (temperature.value === null) {
        return { strokeDasharray: '0, 100' };
      }
      
      // Map temperature (10-50°C) to percentage (0-1)
      // Using 10-50 range to cover typical temperature values
      const minTemp = 10;
      const maxTemp = 50;
      const percentage = Math.min(Math.max((temperature.value - minTemp) / (maxTemp - minTemp), 0), 1);
      
      // strokeDasharray: filled, empty
      // strokeDashoffset: how much to offset from start
      // The circle starts at top (12 o'clock), we want to show progress clockwise
      // Since SVG is rotated -90deg, 0 offset starts at top
      const filled = percentage * 100;
      const empty = 100 - filled;
      
      return {
        strokeDasharray: `${filled}, ${empty}`,
        strokeDashoffset: 0
      };
    });

    const debugInfo = computed(() => {
      return {
        deviceId: props.deviceId,
        clientId: props.clientId,
        connectionStatus: connectionStatus.value,
        temperature: temperature.value,
        rawTemperature: rawTemperature.value,
        messageCount: messageLog.value.length,
        lastMessages: messageLog.value.slice(-5)
      };
    });

    function logMessage(message) {
      const timestamp = new Date().toLocaleTimeString();
      const logEntry = `${timestamp}: ${message}`;
      messageLog.value.push(logEntry);
      console.log(`[DeviceMonitor:${props.deviceId}] ${message}`);
      
      if (messageLog.value.length > 50) {
        messageLog.value.shift();
      }
    }

    async function fetchDeviceState() {
      try {
        const response = await fetch(`${props.apiUrl}/api/merged-map/${props.deviceId}`);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        if (data.payload) {
          const temp = data.payload.temperature ?? null;
          rawTemperature.value = temp !== null ? temp : '--';
          temperature.value = temp;
          isOn.value = data.payload.switchOn ?? false;
          lastUpdateTime.value = data.updatedAt ? new Date(data.updatedAt).toLocaleTimeString() : '--';
          logMessage(`Fetched state - Temp: ${temp}°C, Switch: ${isOn.value ? 'ON' : 'OFF'}`);
        }
      } catch (e) {
        logMessage(`Failed to fetch state: ${e.message}`);
      }
    }

    function connectWebSocket() {
      try {
        logMessage('Connecting to WebSocket...');
        connectionStatus.value = 'connecting';
        
        // Close existing connection if any
        if (ws) {
          ws.close();
          ws = null;
        }
        
        // Clear any existing ping interval
        if (pingInterval) {
          clearInterval(pingInterval);
          pingInterval = null;
        }
        
        ws = new WebSocket(props.wsUrl);
        
        ws.onopen = () => {
          logMessage('Connected to server');
          connectionStatus.value = 'connected';
          
          // Clear any pending reconnect
          if (reconnectTimeout) {
            clearTimeout(reconnectTimeout);
            reconnectTimeout = null;
          }
          
          // Start ping interval to detect connection health
          pingInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
              // Send a ping to keep connection alive and detect drops
              try {
                ws.send(JSON.stringify({ type: 'ping' }));
              } catch (e) {
                // Ignore ping errors
              }
            }
          }, 30000);
        };

        ws.onclose = (event) => {
          logMessage(`Disconnected. Code: ${event.code}, Reason: ${event.reason || 'N/A'}`);
          connectionStatus.value = 'disconnected';
          
          // Clear ping interval
          if (pingInterval) {
            clearInterval(pingInterval);
            pingInterval = null;
          }
          
          // Auto-reconnect after delay unless it was intentional close
          if (!reconnectTimeout && event.code !== 1000) {
            logMessage('Attempting to reconnect...');
            connectionStatus.value = 'connecting';
            reconnectTimeout = setTimeout(connectWebSocket, 3000);
          }
        };

        ws.onerror = (error) => {
          logMessage(`WebSocket error: ${error}`);
          connectionStatus.value = 'disconnected';
        };

        ws.onmessage = (event) => {
          try {
            const message = JSON.parse(event.data);
            handleMessage(message);
          } catch (e) {
            logMessage(`Failed to parse message: ${e.message}`);
          }
        };
      } catch (e) {
        logMessage(`Connection failed: ${e.message}`);
        connectionStatus.value = 'disconnected';
        if (!reconnectTimeout) {
          reconnectTimeout = setTimeout(connectWebSocket, 3000);
        }
      }
    }

    function sendMessage(msg) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(msg));
        logMessage('Sent message: ' + JSON.stringify(msg).substring(0, 100));
        return msg;
      }
      logMessage('Cannot send message: WebSocket not open');
      return null;
    }

    function handleMessage(message) {
      const now = new Date().toLocaleTimeString();
      
      // Handle state-updated broadcast from server
      if (message.type === 'state-updated' && message.id === props.deviceId) {
        const updated = message.updated;
        if (updated && updated.payload) {
          const temp = updated.payload.temperature ?? null;
          rawTemperature.value = temp !== null ? temp : '--';
          temperature.value = temp;
          isOn.value = updated.payload.switchOn ?? false;
          status.value = 'ok';
          lastUpdateTime.value = now;
          logMessage(`Received state-updated - Temp: ${temp}°C, Switch: ${isOn.value ? 'ON' : 'OFF'}`);
        }
        return;
      }
      
      // Handle ack messages
      if (message.type === 'ack' && message.id === props.deviceId) {
        logMessage('Received ACK for device');
        return;
      }
      
      // Handle pong responses
      if (message.type === 'pong') {
        logMessage('Received pong from server');
        return;
      }
      
      logMessage(`Received message type: ${message.type} for id: ${message.id}`);
      
      switch (message.type) {
        case 'device-command':
          // Handle incoming command from server (should be processed by test client)
          if (message.id === props.deviceId) {
            logMessage(`Command received for device: ${message.command}`);
          }
          break;

        case 'device-state-report':
          // Report after executing command
          if (message.requestId) {
            commandHistory.value.push({
              timestamp: now,
              command: message.command || 'unknown',
              status: message.status
            });
            logMessage(`State reported: ${message.command}`);
            
            // Update state from the report if available
            if (message.payload) {
              const temp = message.payload.temperature ?? null;
              if (temp !== null) {
                temperature.value = temp;
                rawTemperature.value = temp;
                lastUpdateTime.value = now;
              }
              const switchOn = message.payload.switchOn ?? null;
              if (switchOn !== null) {
                isOn.value = switchOn;
              }
            }
          }
          break;
          
        default:
          logMessage(`Unhandled message type: ${message.type}`);
      }
    }

    async function toggleSwitch() {
      if (!connected.value) {
        alert('Please wait for connection to establish before toggling switch');
        return;
      }
      
      loading.value = true;
      const newState = !isOn.value;
      
      try {
        // Use HTTP API to toggle switch (as per ws_server_api.md section 3.8)
        const response = await fetch(`${props.apiUrl}/api/device/state`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            id: props.deviceId,
            action: 'toggle'
          })
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }

        const result = await response.json();
        logMessage(`Toggle command sent successfully`);
        
        // Update command history
        commandHistory.value.push({
          timestamp: new Date().toLocaleTimeString(),
          command: 'toggle',
          status: 'ok'
        });

      } catch (error) {
        logMessage(`Toggle failed: ${error.message}`);
        commandHistory.value.push({
          timestamp: new Date().toLocaleTimeString(),
          command: 'toggle',
          status: 'failed'
        });
      } finally {
        loading.value = false;
      }
    }

    onMounted(async () => {
      logMessage(`DeviceMonitor mounted for device: ${props.deviceId}, client: ${props.clientId}`);
      connectWebSocket();
      
      // Fetch initial state
      await fetchDeviceState();
    });

    onUnmounted(() => {
      logMessage('DeviceMonitor unmounting');
      if (pingInterval) {
        clearInterval(pingInterval);
        pingInterval = null;
      }
      if (ws) {
        ws.close();
        ws = null;
      }
      if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
        reconnectTimeout = null;
      }
    });

    return {
      temperature,
      rawTemperature,
      isOn,
      status,
      connectionStatus,
      connected,
      connectionText,
      lastUpdateTime,
      commandHistory,
      loading,
      tempArc,
      tempDashStyle,
      tempColorClass,
      debugInfo,
      toggleSwitch
    };
  }
};
</script>

<style scoped>
.device-monitor-container {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 16px;
  padding: 24px;
  color: white;
  box-shadow: 0 10px 30px rgba(0, 0, 0, 0.3);
  max-width: 400px;
  font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}

.header h3 {
  margin: 0;
  font-size: 1.2rem;
  font-weight: 600;
}

.status-badge {
  font-size: 0.8rem;
  padding: 4px 12px;
  border-radius: 12px;
  font-weight: 500;
}

.status-badge.connecting {
  background-color: rgba(255, 152, 0, 0.2);
  color: #FF9800;
  animation: pulse 1.5s infinite;
}

.status-badge.connected {
  background-color: rgba(76, 175, 80, 0.2);
  color: #4CAF50;
}

.status-badge.disconnected {
  background-color: rgba(244, 67, 54, 0.2);
  color: #f44336;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.temperature-display {
  display: flex;
  justify-content: center;
  margin-bottom: 24px;
}

.temperature-circle {
  position: relative;
  width: 150px;
  height: 150px;
}

.temp-svg {
  transform: rotate(-90deg);
  width: 100%;
  height: 100%;
}

.temp-bg {
  fill: none;
  stroke: rgba(255, 255, 255, 0.1);
  stroke-width: 3;
}

.temp-value {
  fill: none;
  stroke: #FF9800;
  stroke-width: 3;
  stroke-linecap: round;
  transition: stroke-dasharray 0.5s ease;
}

.temp-value.temp-cold {
  stroke: #2196F3;
}

.temp-value.temp-warm {
  stroke: #FF9800;
}

.temp-value.temp-hot {
  stroke: #F44336;
}

.temp-value-text {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  font-size: 2rem;
  font-weight: bold;
  text-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
}

.control-panel {
  background: rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 20px;
}

.info-row {
  display: flex;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 0.9rem;
}

.info-row:last-child {
  margin-bottom: 0;
}

.label {
  opacity: 0.8;
}

.status-value.ok {
  color: #4CAF50;
}

.status-value.error {
  color: #f44336;
}

.switch-control {
  background: rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  padding: 16px;
  margin-bottom: 20px;
}

.switch-label {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  font-size: 0.9rem;
}

.switch-state {
  font-weight: bold;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 0.8rem;
}

.switch-state.ON {
  background: #4CAF50;
}

.switch-state.OFF {
  background: #f44336;
}

.toggle-btn {
  width: 100%;
  padding: 12px;
  border: none;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.2);
  color: white;
  font-size: 1rem;
  cursor: pointer;
  transition: all 0.3s ease;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.toggle-btn:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.3);
}

.toggle-btn.active {
  background: rgba(76, 175, 80, 0.8);
}

.toggle-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn-text {
  font-weight: 500;
}

.btn-icon {
  font-size: 1.2rem;
}

.command-history {
  background: rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  padding: 16px;
}

.command-history h4 {
  margin: 0 0 12px 0;
  font-size: 0.9rem;
  opacity: 0.9;
}

.history-list {
  list-style: none;
  padding: 0;
  margin: 0;
  max-height: 120px;
  overflow-y: auto;
}

.history-list li {
  font-size: 0.8rem;
  padding: 4px 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
}

.history-list li:last-child {
  border-bottom: none;
}

.debug-info {
  background: rgba(0, 0, 0, 0.2);
  border-radius: 12px;
  padding: 16px;
  margin-top: 20px;
}

.debug-info h4 {
  margin: 0 0 12px 0;
  font-size: 0.9rem;
  opacity: 0.9;
}

.debug-info pre {
  font-size: 0.75rem;
  line-height: 1.4;
  overflow-x: auto;
  white-space: pre-wrap;
  word-wrap: break-word;
}
</style>