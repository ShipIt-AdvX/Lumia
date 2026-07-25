/**
 * Lumia Capture Puck — 涂鸦 T5AI（板载麦）+ 电脑讯飞 ASR
 *
 * 配网：LittleFS → 连 WiFi → 探桌宠 /api/health →
 *   失败则 SoftAP「LumiaPuck」密码 12345678 → http://192.168.4.1/
 * 录音：按住 P20 → 松开 WAV → POST /api/capture/audio → 板端清空
 * LED(P9)：按住常亮 → 发送慢闪 → 成功快闪
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
#include "wav_encode.h"

#if __has_include("config.h")
#include "config.h"
#else
#include "config.h.example"
#warning "Using config.h.example — copy to config.h"
#endif

#define MIC_BUFFER_SIZE ((uint32_t)(MAX_RECORD_MS) / 10 * 640)

#ifndef LED_SLOW_MS
#define LED_SLOW_MS 400
#endif
#ifndef LED_FAST_MS
#define LED_FAST_MS 80
#endif
#ifndef LED_SUCCESS_BLINKS
#define LED_SUCCESS_BLINKS 8
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

enum State { IDLE, RECORDING, UPLOADING };

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
static uint32_t recordStartMs = 0;
static bool ledLit = false;
static uint32_t ledLastToggleMs = 0;

static void onButtonEvent(char *name, ButtonEvent_t event, void *arg);
static bool uploadWavToPc(const uint8_t *wav, size_t wavLen);
static void finishAndUpload();
static void handleClient(WiFiClient &client);

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

static void ledBlinkFastSuccess() {
  for (int i = 0; i < LED_SUCCESS_BLINKS; i++) {
    ledSet(true);
    delay(LED_FAST_MS);
    ledSet(false);
    delay(LED_FAST_MS);
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
  /* 不要 WiFi.mode(WIFI_STA)：Tuya 切模式会把已连上的 STA 踢掉 */
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
  /* Tuya：STA 失败常把 SoftAP 冲掉，需重建 */
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
       "/</b><br>按住 P20 录音 · LED P9</p>";
  h += F("<div class=card><h2>状态</h2><p>WiFi：");
  h += (WiFi.status() == WSS_GOT_IP) ? "<span class=ok>已连接 " + htmlEscape(sta) + "</span>"
                                     : "<span class=bad>未连接</span>";
  h += "<br>AP：";
  h += apMode ? "<span class=ok>LumiaPuck</span>" : "关闭";
  h += "<br>桌宠：";
  h += lumiaOk ? "<span class=ok>可达</span>" : "<span class=bad>不可达/未配置</span>";
  h += "<br>上传：<code>" + htmlEscape(lumiaHost) + ":" + String(lumiaPort) +
       "/api/capture/audio</code></p>";
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
    stopAp();
    bool ok = connectWifi(20000);
    if (ok) checkLumia();
    if (!ok || !lumiaOk) startAp();
    return;
  }

  if (path == "/recheck" && isPost) {
    if (WiFi.status() == WSS_GOT_IP) checkLumia();
    if (lumiaOk) stopAp();
    else startAp();
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

void setup() {
  Serial.begin(115200);
  Log.begin();
  PR_NOTICE("======= Lumia Capture Puck (Xfyun via PC) =======");

  if (OPRT_OK != board_register_hardware()) {
    PR_ERR("board_register_hardware failed");
  }

  pinMode(LED_PIN, OUTPUT);
  ledOff();
  loadPrefs();

  portal.begin(80);
  /* 先开配网 AP，再尝试 STA（失败会重建 AP） */
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
    recordButton.setEventCallback(BUTTON_EVENT_PRESS_UP, onButtonEvent);
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
}

void loop() {
  if (apMode) dns.processNextRequest();

  WiFiClient c = portal.available();
  if (c) handleClient(c);

  if (state == RECORDING && (millis() - recordStartMs >= MAX_RECORD_MS)) {
    PR_NOTICE("max record reached");
    finishAndUpload();
  }

  uint32_t now = millis();
  if (now - lastLumiaCheckMs > 15000) {
    lastLumiaCheckMs = now;
    if (WiFi.status() == WSS_GOT_IP) {
      if (checkLumia()) {
        if (apMode) stopAp();
      } else {
        startAp();
      }
    } else {
      lumiaOk = false;
      startAp();
    }
  }
  if (WiFi.status() != WSS_GOT_IP && !wifiSsid.isEmpty()) {
    /* 配网 AP 开启时少打 STA，避免冲掉热点 */
    uint32_t interval = apMode ? 60000UL : 20000UL;
    if (now - lastWifiAttemptMs > interval) {
      lastWifiAttemptMs = now;
      connectWifi(8000);
    }
  }

  delay(10);
}

static void onButtonEvent(char *name, ButtonEvent_t event, void *arg) {
  (void)name;
  (void)arg;

  if (event == BUTTON_EVENT_PRESS_DOWN && state == IDLE && audio.isIdle()) {
    if (WiFi.status() != WSS_GOT_IP) {
      PR_ERR("no WiFi");
      return;
    }
    PR_NOTICE("REC start");
    ledSet(true);
    audio.clearRecordedData();
    if (audio.startRecord() == OPRT_OK) {
      recordStartMs = millis();
      state = RECORDING;
    } else {
      ledOff();
      PR_ERR("startRecord failed");
    }
    return;
  }

  if (event == BUTTON_EVENT_PRESS_UP && state == RECORDING) {
    finishAndUpload();
  }
}

static void finishAndUpload() {
  if (state != RECORDING) return;
  state = UPLOADING;
  ledLastToggleMs = millis();
  ledSet(true);
  audio.stopRecord();

  uint32_t pcmLen = audio.getRecordedDataLen();
  PR_NOTICE("REC stop pcm=%u", (unsigned)pcmLen);

  if (pcmLen < 3200) {
    PR_ERR("too short, discard");
    audio.clearRecordedData();
    ledOff();
    state = IDLE;
    return;
  }

  uint8_t *pcm = (uint8_t *)Malloc(pcmLen);
  if (!pcm) {
    PR_ERR("malloc pcm");
    audio.clearRecordedData();
    ledOff();
    state = IDLE;
    return;
  }
  uint32_t got = audio.readRecordedData(pcm, pcmLen);
  audio.clearRecordedData();
  if (got == 0) {
    Free(pcm);
    ledOff();
    state = IDLE;
    return;
  }

  size_t wavLen = WAV_HEAD_LEN + got;
  uint8_t *wav = (uint8_t *)Malloc(wavLen);
  if (!wav) {
    PR_ERR("malloc wav");
    Free(pcm);
    ledOff();
    state = IDLE;
    return;
  }
  if (OPRT_OK != app_get_wav_head(got, 1, 16000, 16, 1, wav)) {
    Free(pcm);
    Free(wav);
    ledOff();
    state = IDLE;
    return;
  }
  memcpy(wav + WAV_HEAD_LEN, pcm, got);
  Free(pcm);

  bool ok = uploadWavToPc(wav, wavLen);
  Free(wav);
  PR_NOTICE(ok ? "upload OK, board cleared" : "upload FAILED, board cleared");
  if (ok) ledBlinkFastSuccess();
  else ledOff();
  state = IDLE;
}

static bool uploadWavToPc(const uint8_t *wav, size_t wavLen) {
  WiFiClient client;
  ledBlinkTick(LED_SLOW_MS);
  if (!client.connect(lumiaHost.c_str(), lumiaPort)) {
    PR_ERR("connect %s:%u fail", lumiaHost.c_str(), (unsigned)lumiaPort);
    return false;
  }

  const char *boundary = "----LumiaPuck7MA4YWxk";
  char head[256];
  int headLen = snprintf(
      head, sizeof(head),
      "--%s\r\n"
      "Content-Disposition: form-data; name=\"file\"; filename=\"capture.wav\"\r\n"
      "Content-Type: audio/wav\r\n"
      "\r\n",
      boundary);
  char tail[80];
  int tailLen = snprintf(tail, sizeof(tail), "\r\n--%s--\r\n", boundary);
  size_t contentLen = (size_t)headLen + wavLen + (size_t)tailLen;

  client.print("POST /api/capture/audio HTTP/1.1\r\n");
  client.print("Host: ");
  client.print(lumiaHost);
  client.print(":");
  client.print(lumiaPort);
  client.print("\r\n");
  client.print("Content-Type: multipart/form-data; boundary=");
  client.print(boundary);
  client.print("\r\n");
  client.print("Content-Length: ");
  client.print((unsigned)contentLen);
  client.print("\r\n");
  client.print("Connection: close\r\n\r\n");

  client.write((const uint8_t *)head, (size_t)headLen);
  size_t sent = 0;
  while (sent < wavLen) {
    ledBlinkTick(LED_SLOW_MS);
    size_t chunk = wavLen - sent;
    if (chunk > 1024) chunk = 1024;
    size_t n = client.write(wav + sent, chunk);
    if (n == 0) {
      client.stop();
      return false;
    }
    sent += n;
  }
  client.write((const uint8_t *)tail, (size_t)tailLen);

  uint32_t t0 = millis();
  int status = -1;
  while (client.connected() && millis() - t0 < 20000) {
    ledBlinkTick(LED_SLOW_MS);
    String line = client.readStringUntil('\n');
    if (line.startsWith("HTTP/1.")) {
      int sp = line.indexOf(' ');
      if (sp > 0) status = line.substring(sp + 1).toInt();
      break;
    }
    if (!client.available()) delay(5);
  }
  while (client.available()) client.read();
  client.stop();
  PR_NOTICE("HTTP %d", status);
  return status >= 200 && status < 300;
}
