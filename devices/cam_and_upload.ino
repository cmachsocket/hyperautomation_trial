#include <Arduino.h>
#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ==================== 【必须修改】WiFi & 服务器配置 ====================
const char* ssid = "IQOO Z5";
const char* password = "nbk39mptf67ukyp";
const char* serverUrl = "10.132.218.151"; // 上报接口地址

// ==================== 【ESP32-S3-GEEK 专属摄像头引脚】 ====================
#define PWDN_GPIO_NUM     -1
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      10
#define SIOD_GPIO_NUM     17  // 板载I2C SDA
#define SIOC_GPIO_NUM     16  // 板载I2C SCL

#define Y9_GPIO_NUM       13
#define Y8_GPIO_NUM       14
#define Y7_GPIO_NUM       6
#define Y6_GPIO_NUM       4
#define Y5_GPIO_NUM       5
#define Y4_GPIO_NUM       7
#define Y3_GPIO_NUM       8
#define Y2_GPIO_NUM       9

#define VSYNC_GPIO_NUM    11
#define HREF_GPIO_NUM     12
#define PCLK_GPIO_NUM     15

// ==================== 【IoT协议全局变量】 ====================
const char* DEVICE_ID = "esp32-s3-geek-01";  // 设备唯一ID
const char* CLIENT_ID = "camera-client-01";  // 客户端实例ID
uint32_t g_seq = 0;                          // 消息序号，单调递增

// ==================== 【Base64编码工具】 ====================
static const char* base64_chars = 
  "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
  "abcdefghijklmnopqrstuvwxyz"
  "0123456789+/";
//把JPEG二进制数据转换成Base64字符串
String base64_encode(const uint8_t* input, size_t len) {
  String encoded;
  size_t i = 0;
  uint32_t n = 0;
  int pad = 3 - len % 3;
  if (pad == 3) pad = 0;

  for (size_t k = 0; k < len + pad; k++) {
    n = n << 8;
    if (k < len) n |= input[k];
    else n |= 0;

    if ((k + 1) % 3 == 0) {
      encoded += base64_chars[(n >> 18) & 0x3F];
      encoded += base64_chars[(n >> 12) & 0x3F];
      encoded += base64_chars[(n >> 6) & 0x3F];
      encoded += base64_chars[n & 0x3F];
    }
  }

  for (int k = 0; k < pad; k++) {
    encoded[encoded.length() - 1 - k] = '=';
  }
  return encoded;
}

// ==================== 【IoT协议JSON生成】 ====================
String generate_iot_json(const char* status, const String& img_base64, size_t img_size) {
  StaticJsonDocument<2048> doc;

  // 严格按照IoT协议标准添加字段
  doc["id"] = DEVICE_ID;
  doc["client"] = CLIENT_ID;
  doc["seq"] = g_seq++;
  doc["status"] = status;

  // 业务载荷payload：封装图像数据                        //可以自行改动
  JsonObject payload = doc.createNestedObject("payload");
  payload["image_base64"] = img_base64;
  payload["image_size"] = img_size;    //图片大小
  payload["image_type"] = "jpeg";
  payload["timestamp"] = millis();    //时间戳

  String json_str;
  serializeJson(doc, json_str);
  return json_str;
}

// ==================== 【摄像头初始化】 ====================
void camera_init() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;

  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;

  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;

  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;

  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;
  config.frame_size = FRAMESIZE_QVGA;  // 320x240，平衡质量和体积
  config.jpeg_quality = 12;            // 压缩率，12-15之间
  config.fb_count = 2;

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("摄像头初始化失败: 0x%x\n", err);
    return;
  }
  Serial.println("摄像头初始化成功");
}

// ==================== 【setup初始化】 ====================
void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(true);

  // 1. 初始化WiFi
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi连接成功，IP地址: " + WiFi.localIP().toString());

  // 2. 初始化摄像头
  camera_init();
}

// ==================== 【loop主循环】 ====================
void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi断开，重连中...");
    WiFi.reconnect();
    delay(1000);
    return;
  }

  // 1. 采集图像
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    Serial.println("图像采集失败");
    // 上报error状态
    String error_json = generate_iot_json("error", "", 0);
    Serial.println("上报错误报文: " + error_json);
    delay(1000);
    return;
  }

  Serial.printf("采集成功，图像大小: %d 字节\n", fb->len);

  // 2. 图像Base64编码
   String img_base64 = base64_encode(fb->buf, fb->len);
   // 3. 按IoT协议生成JSON报文
   String json_str = generate_iot_json("ok", img_base64, fb->len);
   Serial.println("生成IoT报文: " + json_str);
   // 4. HTTP POST上报到服务器
   HTTPClient http;
   http.begin(serverUrl);
   http.addHeader("Content-Type", "application/json");
   int http_code = http.POST(json_str);
   if (http_code > 0) {
     Serial.printf("上报成功，服务器响应码: %d\n", http_code);
     String response = http.getString();
     Serial.println("服务器返回: " + response);
   } else {
     Serial.printf("上报失败，错误码: %d\n", http_code);
   }
   http.end();
   // 5. 释放图像内存
   esp_camera_fb_return(fb);
   // 5秒上报一次，可根据需求调整
   delay(5000);
 }
