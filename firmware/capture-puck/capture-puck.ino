/**
 * Lumia Capture Puck — 涂鸦 T5AI（板载麦）+ 电脑讯飞 ASR
 *
 * 配网：LittleFS → 连 WiFi → 探桌宠 /api/health →
 *   失败则 SoftAP「LumiaPuck」密码 12345678 → http://192.168.4.1/
 *
 * 录音：WiFi + 桌宠就绪后自动持续录音，PCM 分片实时 POST
 *   /api/capture/stream/chunk（本地环形缓冲读完即丢，不落盘）
 * 断电/断连：板端无法收尾；服务端空闲超时拼 WAV → 讯飞
 *
 * LED(P9)：流式常亮 → 上传慢闪 → 异常熄灭
 * 按钮 P20：短按强制结束当前会话（有电时），再开新会话
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <WiFiServer.h>
#include <WiFiAP.h>
#include <DNSServer.h>
#include "File.h"
#include "Log.h"
#include "board_com_api.h"

#include "Audio.h"
#include "Button.h"

#if __has_include("config.h")
#include "config.h"
#else
#include "config.h.example"
#warning "Using config.h.example — copy to config.h"
#endif

/* 环形缓冲只留约 3s，持续抽走上传，避免占满 */
#ifndef MIC_HOLD_MS
#define MIC_HOLD_MS 3000
#endif
#define MIC_BUFFER_SIZE ((uint32_t)(MIC_HOLD_MS) / 10 * 640)

/* 16kHz * 2bytes * 0.5s ≈ 16000 */
#ifndef STREAM_CHUNK_BYTES
#define STREAM_CHUNK_BYTES 16000
#endif

#ifndef LED_SLOW_MS
#define LED_SLOW_MS 400
#endif
#ifndef LED_FAST_MS
#define LED_FAST_MS 80
#endif
#ifndef AP_SSID
#define AP_SSID "LumiaPuck"
#endif
#ifndef AP_PASS
#define AP_PASS "12345678"
#endif
#ifndef LUMIA_PORT
#define LUMIA_PORT 8787
#endif

enum State { IDLE, STREAMING };

VFSFILE fs(LITTLEFS);
DNSServer dns;
WiFiServer portal;

Audio audio;
Button recordButton;

String wifiSsid, wifiPass, lumiaHost;
uint16_t lumiaPort = LUMIA_PORT;

bool apMode = false;
bool lumiaOk = false;
uint32_t lastLumiaCheckMs = 0;
uint32_t lastWifiAttemptMs = 0;

static State state = IDLE;
static String streamSession;
static bool ledLit = false;
static uint32_t ledLastToggleMs = 0;
static uint8_t chunkBuf[STREAM_CHUNK_BYTES];

static void onButtonEvent(char *name, ButtonEvent_t event, void *arg);
static void handleClient(WiFiClient &client);
static bool startStreaming();
static void stopStreamingLocal(bool notifyEnd);
static void pumpStreamChunks();
static bool postPcmChunk(const uint8_t *data, size_t len);
static bool postStreamEnd();
static bool postStreamStart(String &outSession);

static void ledSet(bool on) {
  ledLit = on;
  digitalWrite(LED_PIN, on ? HIGH : LOW);
}
static void ledOff() { ledSet(false); }

static void ledBlinkTick(uint16_t periodMs) {
  uint32_t now = millis();
  if (now - ledLastToggleMs >= periodMs) {
    ledLastToggleMs = now;
    ledSet(!ledLit);
  }
}

static String urlDecode(String s) {
  String o;
  char a, b;
  for (unsigned i = 0; i < s.length(); i++) {
    if (s[i] == '+') o += ' ';
    else if (s[i] == '%' && i + 2 < s.length()) {
      a = s[i + 1];
      b = s[i + 2];
      if (isxdigit(a) && isxdigit(b)) {
        char hex[3] = {a, b, 0};
        o += (char)strtol(hex, nullptr, 16);
        i += 2;
      } else o += s[i];
    } else o += s[i];
  }
  return o;
}

static String formGet(const String &body, const String &key) {
  String k = key + "=";
  int start = 0;
  while (start >= 0) {
    int p = body.indexOf(k, start);
    if (p < 0) return "";
    if (p > 0 && body[p - 1] != '&' && !(p == 0)) {
      start = p + 1;
      continue;
    }
    if (p == 0 || body[p - 1] == '&') {
      int v = p + k.length();
      int e = body.indexOf('&', v);
      if (e < 0) e = body.length();
      return urlDecode(body.substring(v, e));
    }
    start = p + 1;
  }
  return "";
}

static String htmlEscape(const String &s) {
  String o;
  for (unsigned i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '&') o += "&amp;";
    else if (c == '<') o += "&lt;";
    else if (c == '>') o += "&gt;";
    else if (c == '"') o += "&quot;";
    else o += c;
  }
  return o;
}

static String jsonGetString(const String &body, const char *key) {
  String pat = String("\"") + key + "\"";
  int p = body.indexOf(pat);
  if (p < 0) return "";
  p = body.indexOf(':', p + pat.length());
  if (p < 0) return "";
  p = body.indexOf('"', p + 1);
  if (p < 0) return "";
  int e = body.indexOf('"', p + 1);
  if (e < 0) return "";
  return body.substring(p + 1, e);
}

static void loadPrefs() {
  wifiSsid = WIFI_SSID;
  wifiPass = WIFI_PASS;
  lumiaHost = LUMIA_HOST;
  lumiaPort = LUMIA_PORT;
  if (!fs.exist("/puck.cfg")) {
    PR_NOTICE("prefs seed ssid=%s lumia=%s:%u", wifiSsid.c_str(), lumiaHost.c_str(),
              (unsigned)lumiaPort);
    return;
  }
  TUYA_FILE fd = fs.open("/puck.cfg", "r");
  if (!fd) return;
  char buf[512];
  memset(buf, 0, sizeof(buf));
  fs.readtillN(buf, (int)sizeof(buf) - 1, fd);
  fs.close(fd);
  String text(buf);
  int start = 0;
  while (start < (int)text.length()) {
    int nl = text.indexOf('\n', start);
    if (nl < 0) nl = text.length();
    String line = text.substring(start, nl);
    line.trim();
    int eq = line.indexOf('=');
    if (eq > 0) {
      String k = line.substring(0, eq);
      String v = line.substring(eq + 1);
      if (k == "ssid") wifiSsid = v;
      else if (k == "pass") wifiPass = v;
      else if (k == "lumia_host") lumiaHost = v;
      else if (k == "lumia_port") lumiaPort = (uint16_t)v.toInt();
    }
    start = nl + 1;
  }
  if (lumiaPort == 0) lumiaPort = LUMIA_PORT;
  PR_NOTICE("prefs ssid=%s lumia=%s:%u", wifiSsid.c_str(), lumiaHost.c_str(), (unsigned)lumiaPort);
}

static void savePrefs() {
  String out;
  out += "ssid=" + wifiSsid + "\n";
  out += "pass=" + wifiPass + "\n";
  out += "lumia_host=" + lumiaHost + "\n";
  out += "lumia_port=" + String(lumiaPort) + "\n";
  TUYA_FILE fd = fs.open("/puck.cfg", "tw");
  if (!fd) {
    PR_ERR("save prefs open fail");
    return;
  }
  fs.write(out.c_str(), (int)out.length(), fd);
  fs.close(fd);
  PR_NOTICE("prefs saved");
}

static bool checkLumia() {
  lumiaOk = false;
  if (lumiaHost.isEmpty() || WiFi.status() != WSS_GOT_IP) return false;
  WiFiClient cli;
  cli.setTimeout(2500);
  if (!cli.connect(lumiaHost.c_str(), lumiaPort)) {
    PR_NOTICE("[lumia] connect fail %s:%u", lumiaHost.c_str(), (unsigned)lumiaPort);
    return false;
  }
  cli.print(String("GET /api/health HTTP/1.1\r\nHost: ") + lumiaHost +
            "\r\nConnection: close\r\n\r\n");
  uint32_t t0 = millis();
  String resp;
  while (millis() - t0 < 3000) {
    while (cli.available()) resp += (char)cli.read();
    if (!cli.connected() && !cli.available()) break;
    delay(10);
  }
  cli.stop();
  lumiaOk = resp.indexOf("200") > 0 && resp.indexOf("ok") >= 0;
  PR_NOTICE("[lumia] ok=%d", lumiaOk);
  return lumiaOk;
}

static void startAp() {
  if (apMode) return;
  PR_NOTICE("[ap] %s pass=%s @ 192.168.4.1", AP_SSID, AP_PASS);
  WiFi.mode(WIFI_AP);
  IPAddress ip(192, 168, 4, 1);
  IPAddress gw(192, 168, 4, 1);
  IPAddress mask(255, 255, 255, 0);
  if (!WiFi.softAPConfig(ip, gw, mask)) {
    PR_ERR("softAPConfig failed");
  }
  if (!WiFi.softAP(AP_SSID, AP_PASS)) {
    PR_ERR("softAP failed");
    return;
  }
  PR_NOTICE("[ap] ssid=%s ip=%s", WiFi.softAPSSID().c_str(), WiFi.softAPIP().toString().c_str());
  dns.start(53, "*", ip);
  apMode = true;
}

static void stopAp() {
  if (!apMode) return;
  dns.stop();
  WiFi.softAPdisconnect(false);
  apMode = false;
  PR_NOTICE("[ap] stopped");
}

static bool connectWifi(uint32_t timeoutMs = 20000) {
  if (wifiSsid.isEmpty()) return false;
  PR_NOTICE("[wifi] connecting %s ...", wifiSsid.c_str());
  WiFi.mode(apMode ? WIFI_AP_STA : WIFI_STA);
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
  uint32_t t0 = millis();
  while (WiFi.status() != WSS_GOT_IP && millis() - t0 < timeoutMs) {
    delay(300);
    Serial.print('.');
  }
  Serial.println();
  if (WiFi.status() == WSS_GOT_IP) {
    PR_NOTICE("[wifi] OK %s", WiFi.localIP().toString().c_str());
    return true;
  }
  PR_ERR("[wifi] FAILED");
  if (apMode) {
    apMode = false;
    startAp();
  }
  return false;
}

static String pageHtml() {
  String sta = (WiFi.status() == WSS_GOT_IP) ? WiFi.localIP().toString() : "-";
  String h;
  h.reserve(4200);
  h += F("<!DOCTYPE html><html><head><meta charset=utf-8>"
         "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
         "<title>Lumia 灵感盒</title><style>"
         "body{font-family:system-ui,sans-serif;max-width:520px;margin:24px auto;padding:0 16px;"
         "background:#0f1419;color:#e7ecf1}"
         "h1{font-size:1.25rem}h2{font-size:1rem;margin:20px 0 8px;color:#9db0c0}"
         ".card{background:#1a222c;border-radius:12px;padding:16px;margin:12px 0}"
         "label{display:block;font-size:.85rem;color:#9db0c0;margin:10px 0 4px}"
         "input{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;"
         "border:1px solid #2c3a48;background:#0f1419;color:#e7ecf1}"
         "button{appearance:none;border:0;border-radius:10px;padding:12px 14px;width:100%;"
         "background:#3d8bfd;color:#fff;font-weight:600;margin-top:8px}"
         "button.ghost{background:#2c3a48}"
         ".ok{color:#5ddea6}.bad{color:#ff7b72}.hint{color:#9db0c0;font-size:.85rem}"
         "</style></head><body>");
  h += F("<h1>Lumia · 灵感盒</h1>");
  h += "<p class=hint>配网 AP <b>http://192.168.4.1/</b> · STA <b>http://" + htmlEscape(sta) +
       "/</b><br>连上网+桌宠后<strong>自动持续录音</strong>并实时上传 · LED P9"
       "<br>P20 短按：有电时强制结束本段（送识别）</p>";
  h += F("<div class=card><h2>状态</h2><p>WiFi：");
  h += (WiFi.status() == WSS_GOT_IP) ? "<span class=ok>已连接 " + htmlEscape(sta) + "</span>"
                                     : "<span class=bad>未连接</span>";
  h += "<br>AP：";
  h += apMode ? "<span class=ok>LumiaPuck</span>" : "关闭";
  h += "<br>桌宠：";
  h += lumiaOk ? "<span class=ok>可达</span>" : "<span class=bad>不可达/未配置</span>";
  h += "<br>流式：";
  h += (state == STREAMING) ? "<span class=ok>录音中</span>" : "<span class=bad>等待</span>";
  if (streamSession.length()) {
    h += "<br>会话：<code>" + htmlEscape(streamSession) + "</code>";
  }
  h += "<br>上传：<code>" + htmlEscape(lumiaHost) + ":" + String(lumiaPort) +
       "/api/capture/stream/chunk</code></p>";
  h += F("<form method=POST action=/recheck><button class=ghost type=submit>重新探测桌宠</button>"
         "</form></div>");

  h += F("<div class=card><h2>配网</h2><form method=POST action=/save>");
  h += "<label>WiFi SSID（2.4G）</label><input name=ssid value=\"" + htmlEscape(wifiSsid) +
       "\" required>";
  h += "<label>WiFi 密码</label><input name=pass type=password value=\"" + htmlEscape(wifiPass) +
       "\">";
  h += "<label>桌宠 / 电脑 IP</label><input name=lumia_host value=\"" + htmlEscape(lumiaHost) +
       "\" required>";
  h += "<label>端口</label><input name=lumia_port type=number value=\"" + String(lumiaPort) + "\">";
  h += F("<button type=submit>保存并应用</button></form></div>");
  h += F("<p class=hint>热点 SSID <b>LumiaPuck</b> · 密码 <b>12345678</b></p></body></html>");
  return h;
}

static void sendText(WiFiClient &c, int code, const char *ctype, const String &body) {
  c.print("HTTP/1.1 ");
  c.print(code);
  c.println(code == 200 ? " OK" : code == 302 ? " Found" : " Error");
  c.print("Content-Type: ");
  c.println(ctype);
  c.print("Content-Length: ");
  c.println(body.length());
  c.println("Connection: close");
  c.println();
  c.print(body);
}

static void handleClient(WiFiClient &client) {
  String reqLine;
  String headers;
  uint32_t t0 = millis();
  while (client.connected() && millis() - t0 < 3000) {
    if (!client.available()) {
      delay(1);
      continue;
    }
    String line = client.readStringUntil('\n');
    line.trim();
    if (reqLine.length() == 0) reqLine = line;
    if (line.length() == 0) break;
    headers += line + "\n";
  }

  int contentLen = 0;
  int cl = headers.indexOf("Content-Length:");
  if (cl < 0) cl = headers.indexOf("content-length:");
  if (cl >= 0) {
    int e = headers.indexOf('\n', cl);
    contentLen = headers.substring(cl + 15, e).toInt();
  }
  String body;
  t0 = millis();
  while ((int)body.length() < contentLen && millis() - t0 < 3000) {
    while (client.available() && (int)body.length() < contentLen) body += (char)client.read();
    if (!client.available()) delay(1);
  }

  PR_NOTICE("HTTP %s", reqLine.c_str());
  bool isPost = reqLine.startsWith("POST ");
  String path;
  int sp1 = reqLine.indexOf(' ');
  int sp2 = reqLine.indexOf(' ', sp1 + 1);
  if (sp1 >= 0 && sp2 > sp1) path = reqLine.substring(sp1 + 1, sp2);
  int q = path.indexOf('?');
  if (q >= 0) path = path.substring(0, q);

  if (path == "/save" && isPost) {
    String v;
    v = formGet(body, "ssid");
    if (v.length()) wifiSsid = v;
    v = formGet(body, "pass");
    wifiPass = v;
    v = formGet(body, "lumia_host");
    if (v.length()) lumiaHost = v;
    v = formGet(body, "lumia_port");
    if (v.length()) lumiaPort = (uint16_t)v.toInt();
    if (lumiaPort == 0) lumiaPort = LUMIA_PORT;
    savePrefs();
    sendText(client, 200, "text/html; charset=utf-8",
             F("<!DOCTYPE html><meta charset=utf-8><body style='font-family:sans-serif;padding:24px;"
               "background:#0f1419;color:#e7ecf1'>已保存，正在应用…"
               "<script>setTimeout(()=>location.href='/',1500)</script></body>"));
    client.stop();
    delay(200);
    stopStreamingLocal(true);
    stopAp();
    bool ok = connectWifi(20000);
    if (ok) checkLumia();
    if (!ok || !lumiaOk) startAp();
    return;
  }

  if (path == "/recheck" && isPost) {
    if (WiFi.status() == WSS_GOT_IP) checkLumia();
    if (lumiaOk) stopAp();
    else {
      stopStreamingLocal(false);
      startAp();
    }
    client.println("HTTP/1.1 302 Found");
    client.println("Location: /");
    client.println("Content-Length: 0");
    client.println("Connection: close");
    client.println();
    client.stop();
    return;
  }

  if (path == "/api/status") {
    String j = "{";
    j += "\"wifi\":" + String(WiFi.status() == WSS_GOT_IP ? "true" : "false") + ",";
    j += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
    j += "\"ap\":" + String(apMode ? "true" : "false") + ",";
    j += "\"lumia_ok\":" + String(lumiaOk ? "true" : "false") + ",";
    j += "\"streaming\":" + String(state == STREAMING ? "true" : "false") + ",";
    j += "\"session\":\"" + streamSession + "\",";
    j += "\"host\":\"" + lumiaHost + "\",";
    j += "\"port\":" + String(lumiaPort);
    j += "}";
    sendText(client, 200, "application/json", j);
    client.stop();
    return;
  }

  sendText(client, 200, "text/html; charset=utf-8", pageHtml());
  client.stop();
}

static bool httpExchange(const char *method, const String &path, const uint8_t *body, size_t bodyLen,
                         const char *contentType, String &respOut, int &statusOut) {
  respOut = "";
  statusOut = -1;
  WiFiClient client;
  client.setTimeout(8000);
  if (!client.connect(lumiaHost.c_str(), lumiaPort)) {
    PR_ERR("connect %s:%u fail", lumiaHost.c_str(), (unsigned)lumiaPort);
    return false;
  }
  client.print(method);
  client.print(" ");
  client.print(path);
  client.print(" HTTP/1.1\r\nHost: ");
  client.print(lumiaHost);
  client.print(":");
  client.print(lumiaPort);
  client.print("\r\n");
  if (streamSession.length()) {
    client.print("X-Lumia-Session: ");
    client.print(streamSession);
    client.print("\r\n");
  }
  if (contentType) {
    client.print("Content-Type: ");
    client.print(contentType);
    client.print("\r\n");
  }
  client.print("Content-Length: ");
  client.print((unsigned)bodyLen);
  client.print("\r\nConnection: close\r\n\r\n");
  if (body && bodyLen) {
    size_t sent = 0;
    while (sent < bodyLen) {
      ledBlinkTick(LED_SLOW_MS);
      size_t n = bodyLen - sent;
      if (n > 1024) n = 1024;
      size_t w = client.write(body + sent, n);
      if (w == 0) {
        client.stop();
        return false;
      }
      sent += w;
    }
  }
  uint32_t t0 = millis();
  while (client.connected() && millis() - t0 < 15000) {
    while (client.available()) {
      String line = client.readStringUntil('\n');
      if (statusOut < 0 && line.startsWith("HTTP/1.")) {
        int sp = line.indexOf(' ');
        if (sp > 0) statusOut = line.substring(sp + 1).toInt();
      } else if (line.length() <= 1) {
        /* headers done */
        while (client.available()) respOut += (char)client.read();
        t0 = millis();
      } else if (statusOut >= 0 && line.length() > 1) {
        /* skip header lines */
      }
    }
    if (!client.connected() && !client.available()) break;
    delay(2);
  }
  while (client.available()) respOut += (char)client.read();
  client.stop();
  return statusOut >= 200 && statusOut < 300;
}

static bool postStreamStart(String &outSession) {
  outSession = "";
  String resp;
  int status = -1;
  if (!httpExchange("POST", "/api/capture/stream/start", nullptr, 0, "application/json", resp,
                    status)) {
    return false;
  }
  outSession = jsonGetString(resp, "session");
  if (!outSession.length()) {
    /* 兜底本地会话 id */
    char buf[40];
    snprintf(buf, sizeof(buf), "puck-%lu-%u", (unsigned long)millis(), (unsigned)random(0xffff));
    outSession = buf;
  }
  return true;
}

static bool postPcmChunk(const uint8_t *data, size_t len) {
  if (!streamSession.length() || !data || !len) return false;
  String path = "/api/capture/stream/chunk?session=" + streamSession;
  String resp;
  int status = -1;
  return httpExchange("POST", path, data, len, "application/octet-stream", resp, status);
}

static bool postStreamEnd() {
  if (!streamSession.length()) return false;
  String path = "/api/capture/stream/end?session=" + streamSession;
  String resp;
  int status = -1;
  return httpExchange("POST", path, nullptr, 0, "application/json", resp, status);
}

static bool startStreaming() {
  if (state == STREAMING) return true;
  if (WiFi.status() != WSS_GOT_IP || !lumiaOk) return false;
  if (!audio.isIdle() && !audio.isRecording()) {
    /* 忙则跳过 */
  }
  String sid;
  if (!postStreamStart(sid)) {
    PR_ERR("stream start fail");
    lumiaOk = false;
    return false;
  }
  streamSession = sid;
  audio.clearRecordedData();
  if (audio.startRecord() != OPRT_OK) {
    PR_ERR("startRecord failed");
    streamSession = "";
    return false;
  }
  state = STREAMING;
  ledSet(true);
  PR_NOTICE("STREAM start session=%s", streamSession.c_str());
  return true;
}

static void stopStreamingLocal(bool notifyEnd) {
  if (state != STREAMING && streamSession.length() == 0) return;
  if (audio.isRecording()) audio.stopRecord();
  /* 尽量抽干残余并上传 */
  if (WiFi.status() == WSS_GOT_IP && streamSession.length()) {
    for (int i = 0; i < 8; i++) {
      uint32_t avail = audio.getRecordedDataLen();
      if (avail == 0) break;
      uint32_t n = avail;
      if (n > STREAM_CHUNK_BYTES) n = STREAM_CHUNK_BYTES;
      uint32_t got = audio.readRecordedData(chunkBuf, n);
      if (got == 0) break;
      if (!postPcmChunk(chunkBuf, got)) break;
    }
    if (notifyEnd) postStreamEnd();
  } else {
    audio.clearRecordedData();
  }
  streamSession = "";
  state = IDLE;
  ledOff();
  PR_NOTICE("STREAM stop (notify=%d)", notifyEnd);
}

static void pumpStreamChunks() {
  if (state != STREAMING) return;
  while (true) {
    uint32_t avail = audio.getRecordedDataLen();
    if (avail < (STREAM_CHUNK_BYTES / 2)) break;
    uint32_t n = avail;
    if (n > STREAM_CHUNK_BYTES) n = STREAM_CHUNK_BYTES;
    uint32_t got = audio.readRecordedData(chunkBuf, n);
    if (got == 0) break;
    if (!postPcmChunk(chunkBuf, got)) {
      PR_ERR("chunk upload fail — pause stream, wait reconnect");
      if (audio.isRecording()) audio.stopRecord();
      audio.clearRecordedData();
      streamSession = "";
      state = IDLE;
      lumiaOk = false;
      ledOff();
      break;
    }
    ledSet(true);
  }
}

void setup() {
  Serial.begin(115200);
  Log.begin();
  PR_NOTICE("======= Lumia Capture Puck (stream → PC ASR) =======");

  if (OPRT_OK != board_register_hardware()) {
    PR_ERR("board_register_hardware failed");
  }

  pinMode(LED_PIN, OUTPUT);
  ledOff();
  loadPrefs();
  randomSeed(micros());

  portal.begin(80);
  startAp();

  bool wifiOk = connectWifi(12000);
  if (wifiOk) {
    checkLumia();
    if (lumiaOk) stopAp();
  } else if (!apMode) {
    startAp();
  }

  ButtonConfig_t btnCfg = {50, 2000, 500, 2, 300};
  PinConfig_t pinCfg = {BUTTON_PIN, TUYA_GPIO_LEVEL_LOW, TUYA_GPIO_PULLUP};
  if (recordButton.begin("Rec", pinCfg, btnCfg) != OPRT_OK) {
    PR_ERR("button GPIO%d failed", BUTTON_PIN);
  } else {
    recordButton.setEventCallback(BUTTON_EVENT_PRESS_DOWN, onButtonEvent);
  }

  AudioConfig cfg;
  cfg.micBufferSize = MIC_BUFFER_SIZE;
  cfg.volume = 70;
  cfg.enableAEC = false;
  if (audio.begin(&cfg) != OPRT_OK) {
    PR_ERR("audio.begin failed");
    return;
  }

  PR_NOTICE("portal :80  ap=%d lumia=%d btn=P%d led=P%d → %s:%u", apMode, lumiaOk, BUTTON_PIN,
            LED_PIN, lumiaHost.c_str(), (unsigned)lumiaPort);
  lastWifiAttemptMs = millis();
  lastLumiaCheckMs = millis();

  if (lumiaOk) startStreaming();
}

void loop() {
  if (apMode) dns.processNextRequest();

  WiFiClient c = portal.available();
  if (c) handleClient(c);

  if (state == STREAMING) pumpStreamChunks();

  uint32_t now = millis();
  if (now - lastLumiaCheckMs > 15000) {
    lastLumiaCheckMs = now;
    if (WiFi.status() == WSS_GOT_IP) {
      bool ok = checkLumia();
      if (ok) {
        if (apMode) stopAp();
        if (state != STREAMING) startStreaming();
      } else {
        stopStreamingLocal(false);
        startAp();
      }
    } else {
      lumiaOk = false;
      stopStreamingLocal(false);
      startAp();
    }
  }
  if (WiFi.status() != WSS_GOT_IP && !wifiSsid.isEmpty()) {
    uint32_t interval = apMode ? 60000UL : 20000UL;
    if (now - lastWifiAttemptMs > interval) {
      lastWifiAttemptMs = now;
      connectWifi(8000);
    }
  }

  /* 已连上但还没开流 */
  if (state == IDLE && lumiaOk && WiFi.status() == WSS_GOT_IP && audio.isIdle()) {
    startStreaming();
  }

  delay(10);
}

static void onButtonEvent(char *name, ButtonEvent_t event, void *arg) {
  (void)name;
  (void)arg;
  if (event != BUTTON_EVENT_PRESS_DOWN) return;
  /* 短按：有电时强制结束本段 → 服务端立刻 ASR，然后开新会话 */
  if (state == STREAMING) {
    PR_NOTICE("button: finalize session");
    stopStreamingLocal(true);
    delay(200);
    if (lumiaOk) startStreaming();
  }
}
