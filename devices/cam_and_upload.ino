#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>

// ===================== WiFi 和服务器配置 =====================
const char* ssid     = "你的WiFi名称";
const char* password = "你的WiFi密码";
const char* serverUrl = "http://你的服务器地址/api/upload"; // 上传接口

// ===================== ESP32-CAM 摄像头引脚（标准） =====================
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27

#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5

#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

// ===================== 【按你的IoT标准配置】 =====================
const char* DEVICE_ID  = "device-0";    // 设备唯一标识
const char* CLIENT_ID  = "client-0";    // 客户端实例标识
uint32_t seq_counter = 0;               // 消息序号，单调递增

// ===================== Base64 编码（二进制图片转字符串） =====================
static const char* base64_chars =
"ABCDEFGHIJKLMNOPQRSTUVWXYZ"
"abcdefghijklmnopqrstuvwxyz"
"0123456789+/";

String base64_encode(const uint8_t* input, size_t len) {
    String encoded;
    size_t i = 0;
    uint32_t n = 0;
    int pad = 3 - len % 3;
    if (pad == 3) pad = 0;

    for (size_t k = 0; k < len + pad; k++) {
        n <<= 8;
        if (k < len) {
            n |= input[k];
        } else {
            n |= 0;
        }

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

// ===================== 【按你的报文标准封装JSON】 =====================
String build_iot_json(const char* status, const String& img_base64, size_t img_size) {
    StaticJsonDocument<2048> doc;

    // 顶层字段，和文档完全一致
    doc["id"]      = DEVICE_ID;
    doc["client"]  = CLIENT_ID;
    doc["seq"]     = seq_counter++;
    doc["status"]  = status;

    // payload：业务数据区，你可以在这里自定义
    JsonObject payload = doc.createNestedObject("payload");
    payload["image_base64"] = img_base64;
    payload["image_size"]   = img_size;
    payload["image_type"]   = "jpeg";
    payload["timestamp"]    = millis();

    String json;
    serializeJson(doc, json);
    return json;
}

// ===================== 摄像头初始化 =====================
bool camera_init() {
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer   = LEDC_TIMER_0;

    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;

    config.pin_xclk    = XCLK_GPIO_NUM;
    config.pin_pclk    = PCLK_GPIO_NUM;
    config.pin_vsync   = VSYNC_GPIO_NUM;
    config.pin_href    = HREF_GPIO_NUM;
    config.pin_sscb_sda= SIOD_GPIO_NUM;
    config.pin_sscb_scl= SIOC_GPIO_NUM;
    config.pin_pwdn    = PWDN_GPIO_NUM;
    config.pin_reset   = RESET_GPIO_NUM;

    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;
    config.frame_size   = FRAMESIZE_QVGA; // 320x240，可改为VGA等
    config.jpeg_quality = 12;             // 0-63，越小越清晰
    config.fb_count     = 1;

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK) {
        Serial.printf("摄像头初始化失败: 0x%x\n", err);
        return false;
    }
    Serial.println("摄像头初始化成功");
    return true;
}

// ===================== setup =====================
void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("ESP32-CAM 启动...");

    // 连接 WiFi
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println("\nWiFi 连接成功，IP: " + WiFi.localIP().toString());

    // 初始化摄像头
    if (!camera_init()) {
        while (1);
    }
}

// ===================== loop =====================
void loop() {
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi 断开，重连中...");
        WiFi.reconnect();
        delay(1000);
        return;
    }

    // 1. 采集一张图片
    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb) {
        Serial.println("采集失败，上报 error");
         String json = build_iot_json("error", "", 0);
         Serial.println(json);
         delay(1000);
         return;
     }
     Serial.printf("采集成功，大小: %d 字节\n", fb->len);
     // 2. 图片转 Base64
     String img_base64 = base64_encode(fb->buf, fb->len);
     // 3. 按你给的格式封装成 JSON
     String json = build_iot_json("ok", img_base64, fb->len);
     Serial.println("上报报文: ");
     Serial.println(json);
     // 4. HTTP POST 上传到服务器
     HTTPClient http;
     http.begin(serverUrl);
     http.addHeader("Content-Type", "application/json");
     int httpCode = http.POST(json);
     if (httpCode > 0) {
         Serial.printf("服务器响应码: %d\n", httpCode);
         Serial.println("返回: " + http.getString());
     } else {
         Serial.println("上传失败: " + http.errorToString(httpCode));
     }
     http.end();
     // 5. 释放缓存，防止内存泄漏
     esp_camera_fb_return(fb);
     delay(5000); // 采集间隔，单位毫秒，可自行修改
}