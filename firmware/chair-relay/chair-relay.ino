/*
 * Lumia Chair Relay — LiberNovo 扶手键并联（开漏点动）
 *
 * 启动：读 NVS → 连 WiFi → 探活桌宠(OrangePi Lumia) →
 *   失败则开 SoftAP「LiberNovo」（无密码），固定 http://192.168.4.1/
 *
 * 配网页：WiFi / 桌宠 IP·端口 / A7–A4 开漏测试（按住=拉低，松开=高阻）
 * 业务：POST/GET :8790/stretch 点动「拉伸」脚（默认 A7）
 *
 * 接线：各脚并联到椅键信号端（按下为接地的那端），GND 共地。
 * 空闲 = INPUT（高阻），点动 = OUTPUT LOW。勿长按。
 */

#include <WiFi.h>
#include <WebServer.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <HTTPClient.h>
#include <ESPmDNS.h>

#if __has_include("config.h")
#include "config.h"
#else
#include "config.h.example"
#endif

/* Waveshare / Arduino Nano ESP32：A7=14 A6=13 A5=12 A4=11（可被 config.h / -D 覆盖） */
#ifndef PIN_A7
#define PIN_A7 14
#endif
#ifndef PIN_A6
#define PIN_A6 13
#endif
#ifndef PIN_A5
#define PIN_A5 12
#endif
#ifndef PIN_A4
#define PIN_A4 11
#endif

#ifndef AP_SSID
#define AP_SSID "LiberNovo"
#endif
#ifndef AP_PASS
#define AP_PASS ""
#endif
#ifndef DEFAULT_PET_PORT
#define DEFAULT_PET_PORT 8787
#endif
#ifndef DEFAULT_PULSE_MS
#define DEFAULT_PULSE_MS 300
#endif

static const int PIN_BY_NAME[4] = {PIN_A7, PIN_A6, PIN_A5, PIN_A4};
static const char *const NAME_BY_IDX[4] = {"A7", "A6", "A5", "A4"};

Preferences prefs;
DNSServer dns;
WebServer portal(80);   // 配网 / 测试
WebServer relay(8790);  // Lumia LUMIA_CHAIR_RELAY_URL

String wifiSsid, wifiPass, petHost;
uint16_t petPort = DEFAULT_PET_PORT;
uint16_t pulseMs = DEFAULT_PULSE_MS;
uint8_t stretchIdx = 0;  // 0=A7 …

bool apMode = false;
bool petOk = false;
uint32_t lastPetCheckMs = 0;
uint32_t lastWifiAttemptMs = 0;

static int pinIndexFromArg(const String &s) {
  if (s == "A7" || s == "a7" || s == "0") return 0;
  if (s == "A6" || s == "a6" || s == "1") return 1;
  if (s == "A5" || s == "a5" || s == "2") return 2;
  if (s == "A4" || s == "a4" || s == "3") return 3;
  return -1;
}

static void pinFloat(int gpio) {
  pinMode(gpio, INPUT);  // 高阻，不抢椅原厂键
}

static void pinLow(int gpio) {
  pinMode(gpio, OUTPUT);
  digitalWrite(gpio, LOW);
}

static void allFloat() {
  for (int i = 0; i < 4; i++) pinFloat(PIN_BY_NAME[i]);
}

static void pulsePin(int idx) {
  if (idx < 0 || idx > 3) return;
  uint16_t ms = pulseMs;
  if (ms < 80) ms = 80;
  if (ms > 800) ms = 800;
  int gpio = PIN_BY_NAME[idx];
  Serial.printf("[pulse] %s gpio=%d %ums\n", NAME_BY_IDX[idx], gpio, ms);
  pinLow(gpio);
  delay(ms);
  pinFloat(gpio);
}

static void loadPrefs() {
  prefs.begin("lumia-chair", true);
  wifiSsid = prefs.getString("ssid", "");
  wifiPass = prefs.getString("pass", "");
  petHost = prefs.getString("pet_host", "");
  petPort = prefs.getUShort("pet_port", DEFAULT_PET_PORT);
  pulseMs = prefs.getUShort("pulse_ms", DEFAULT_PULSE_MS);
  stretchIdx = prefs.getUChar("stretch_i", 0);
  if (stretchIdx > 3) stretchIdx = 0;
  prefs.end();
}

static void savePrefs() {
  prefs.begin("lumia-chair", false);
  prefs.putString("ssid", wifiSsid);
  prefs.putString("pass", wifiPass);
  prefs.putString("pet_host", petHost);
  prefs.putUShort("pet_port", petPort);
  prefs.putUShort("pulse_ms", pulseMs);
  prefs.putUChar("stretch_i", stretchIdx);
  prefs.end();
}

static bool checkPet() {
  if (petHost.isEmpty()) {
    petOk = false;
    return false;
  }
  if (WiFi.status() != WL_CONNECTED) {
    petOk = false;
    return false;
  }
  HTTPClient http;
  String url = "http://" + petHost + ":" + String(petPort) + "/api/health";
  http.setConnectTimeout(2500);
  http.setTimeout(2500);
  if (!http.begin(url)) {
    petOk = false;
    return false;
  }
  int code = http.GET();
  String body = (code > 0) ? http.getString() : "";
  http.end();
  petOk = (code == 200 && body.indexOf("ok") >= 0);
  Serial.printf("[pet] %s => %d ok=%d\n", url.c_str(), code, petOk);
  return petOk;
}

static void startAp() {
  if (apMode) return;
  Serial.println("[ap] LiberNovo open @ 192.168.4.1");
  WiFi.mode(WIFI_AP_STA);
  IPAddress ip(AP_LOCAL_IP);
  IPAddress gw(AP_LOCAL_IP);
  IPAddress mask(255, 255, 255, 0);
  WiFi.softAPConfig(ip, gw, mask);
  if (strlen(AP_PASS) == 0) {
    WiFi.softAP(AP_SSID);
  } else {
    WiFi.softAP(AP_SSID, AP_PASS);
  }
  dns.start(53, "*", ip);
  apMode = true;
}

static void stopAp() {
  if (!apMode) return;
  dns.stop();
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_STA);
  apMode = false;
  Serial.println("[ap] stopped");
}

static bool connectWifi(uint32_t timeoutMs = 15000) {
  if (wifiSsid.isEmpty()) return false;
  Serial.printf("[wifi] connecting %s ...\n", wifiSsid.c_str());
  WiFi.mode(WIFI_STA);
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < timeoutMs) {
    delay(300);
    Serial.print('.');
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] OK %s\n", WiFi.localIP().toString().c_str());
    return true;
  }
  Serial.println("[wifi] FAILED");
  return false;
}

static String htmlEscape(const String &s) {
  String o;
  o.reserve(s.length());
  for (size_t i = 0; i < s.length(); i++) {
    char c = s[i];
    if (c == '&') o += "&amp;";
    else if (c == '<') o += "&lt;";
    else if (c == '>') o += "&gt;";
    else if (c == '"') o += "&quot;";
    else o += c;
  }
  return o;
}

static String pageHtml() {
  String sta = (WiFi.status() == WL_CONNECTED) ? WiFi.localIP().toString() : "-";
  String apip = apMode ? WiFi.softAPIP().toString() : "-";
  String stretchName = NAME_BY_IDX[stretchIdx];

  String h;
  h.reserve(6500);
  h += F("<!DOCTYPE html><html><head><meta charset=utf-8>"
         "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
         "<title>LiberNovo 椅控配网</title><style>"
         "body{font-family:system-ui,sans-serif;max-width:520px;margin:24px auto;padding:0 16px;"
         "background:#0f1419;color:#e7ecf1}"
         "h1{font-size:1.25rem;margin:0 0 8px}h2{font-size:1rem;margin:24px 0 8px;color:#9db0c0}"
         ".card{background:#1a222c;border-radius:12px;padding:16px;margin:12px 0}"
         "label{display:block;font-size:.85rem;color:#9db0c0;margin:10px 0 4px}"
         "input,select{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;"
         "border:1px solid #2c3a48;background:#0f1419;color:#e7ecf1}"
         "button,.btn{appearance:none;border:0;border-radius:10px;padding:12px 14px;"
         "background:#3d8bfd;color:#fff;font-weight:600;cursor:pointer;width:100%;margin-top:8px}"
         "button.ghost{background:#2c3a48}.row{display:grid;grid-template-columns:1fr 1fr;gap:8px}"
         ".ok{color:#5ddea6}.bad{color:#ff7b72}.hint{color:#9db0c0;font-size:.85rem;line-height:1.4}"
         ".hold{user-select:none;-webkit-user-select:none;background:#c27a2c}"
         ".hold:active,.hold.on{background:#e3a23a}"
         "</style></head><body>");
  h += F("<h1>LiberNovo · 椅控</h1>");
  h += "<p class=hint>配网页：AP <b>http://192.168.4.1/</b> · STA <b>http://" + htmlEscape(sta) +
       "/</b><br>拉伸接口：<code>http://&lt;esp&gt;:8790/stretch</code></p>";

  h += F("<div class=card><h2>状态</h2>");
  h += "<p>WiFi：";
  h += (WiFi.status() == WL_CONNECTED) ? "<span class=ok>已连接 " + htmlEscape(sta) + "</span>"
                                       : "<span class=bad>未连接</span>";
  h += "<br>AP：";
  h += apMode ? "<span class=ok>LiberNovo @ " + htmlEscape(apip) + "</span>" : "关闭";
  h += "<br>桌宠：";
  h += petOk ? "<span class=ok>可达</span>" : "<span class=bad>不可达 / 未配置</span>";
  h += "<br>拉伸脚：<b>" + String(stretchName) + "</b> · 点动 " + String(pulseMs) + " ms</p>";
  h += F("<form method=POST action=/api/recheck><button class=ghost type=submit>重新探测桌宠</button></form>"
         "</div>");

  h += F("<div class=card><h2>配网</h2><form method=POST action=/save>");
  h += "<label>WiFi SSID</label><input name=ssid value=\"" + htmlEscape(wifiSsid) + "\" required>";
  h += "<label>WiFi 密码</label><input name=pass type=password value=\"" + htmlEscape(wifiPass) + "\">";
  h += "<label>桌宠 / OrangePi IP</label><input name=pet_host value=\"" + htmlEscape(petHost) +
       "\" placeholder=\"10.x.x.x\" required>";
  h += "<label>桌宠端口（Lumia）</label><input name=pet_port type=number value=\"" + String(petPort) +
       "\">";
  h += "<label>拉伸对应脚</label><select name=stretch_i>";
  for (int i = 0; i < 4; i++) {
    h += "<option value=" + String(i);
    if (i == stretchIdx) h += " selected";
    h += ">" + String(NAME_BY_IDX[i]) + " (GPIO " + String(PIN_BY_NAME[i]) + ")</option>";
  }
  h += "</select>";
  h += "<label>点动时长 ms（80–800）</label><input name=pulse_ms type=number value=\"" +
       String(pulseMs) + "\">";
  h += F("<button type=submit>保存并重启</button></form>"
         "<p class=hint>保存后会重启；连上 WiFi 且桌宠 "
         "<code>/api/health</code> 正常后会关掉 AP。</p></div>");

  h += F("<div class=card><h2>按键测试（开漏）</h2>"
         "<p class=hint>按住 = 该脚拉低（模拟按下椅键）；松开 = 悬空。"
         "请确认椅在安全姿态后再测。</p><div class=row>");
  for (int i = 0; i < 4; i++) {
    h += "<button type=button class=\"hold\" data-pin=\"" + String(NAME_BY_IDX[i]) + "\">按住测 " +
         String(NAME_BY_IDX[i]) + "</button>";
  }
  h += F("</div>"
         "<button type=button class=ghost id=pulseStretch style=\"margin-top:12px\">点动拉伸脚一次</button>"
         "</div>");

  h += F("<script>"
         "async function pin(name,down){"
         "fetch('/api/pin?name='+name+'&state='+(down?'low':'float'),{method:'POST'});}"
         "document.querySelectorAll('.hold').forEach(b=>{"
         "const n=b.dataset.pin;"
         "const go=e=>{e.preventDefault();b.classList.add('on');pin(n,true)};"
         "const up=e=>{e.preventDefault();b.classList.remove('on');pin(n,false)};"
         "b.addEventListener('mousedown',go);b.addEventListener('mouseup',up);"
         "b.addEventListener('mouseleave',up);"
         "b.addEventListener('touchstart',go,{passive:false});"
         "b.addEventListener('touchend',up);b.addEventListener('touchcancel',up);"
         "});"
         "document.getElementById('pulseStretch').onclick=()=>fetch('/stretch',{method:'POST'});"
         "</script></body></html>");
  return h;
}

static void handleRoot() { portal.send(200, "text/html; charset=utf-8", pageHtml()); }

static void handleSave() {
  if (portal.hasArg("ssid")) wifiSsid = portal.arg("ssid");
  if (portal.hasArg("pass")) wifiPass = portal.arg("pass");
  if (portal.hasArg("pet_host")) petHost = portal.arg("pet_host");
  if (portal.hasArg("pet_port")) petPort = (uint16_t)portal.arg("pet_port").toInt();
  if (portal.hasArg("pulse_ms")) pulseMs = (uint16_t)portal.arg("pulse_ms").toInt();
  if (portal.hasArg("stretch_i")) stretchIdx = (uint8_t)portal.arg("stretch_i").toInt();
  if (petPort == 0) petPort = DEFAULT_PET_PORT;
  if (stretchIdx > 3) stretchIdx = 0;
  if (pulseMs < 80) pulseMs = 80;
  if (pulseMs > 800) pulseMs = 800;
  savePrefs();
  portal.send(200, "text/html; charset=utf-8",
              F("<!DOCTYPE html><meta charset=utf-8><body style='font-family:sans-serif;padding:24px'>"
                "已保存，正在重启…<script>setTimeout(()=>location.href='/',2500)</script></body>"));
  delay(500);
  ESP.restart();
}

static void handlePin() {
  int idx = pinIndexFromArg(portal.arg("name"));
  String st = portal.arg("state");
  if (idx < 0) {
    portal.send(400, "application/json", "{\"ok\":false,\"error\":\"bad pin\"}");
    return;
  }
  if (st == "low" || st == "0" || st == "down") {
    pinLow(PIN_BY_NAME[idx]);
  } else {
    pinFloat(PIN_BY_NAME[idx]);
  }
  portal.send(200, "application/json",
              String("{\"ok\":true,\"pin\":\"") + NAME_BY_IDX[idx] + "\",\"state\":\"" + st + "\"}");
}

static void handleRecheck() {
  checkPet();
  if (petOk && WiFi.status() == WL_CONNECTED) stopAp();
  else startAp();
  portal.sendHeader("Location", "/", true);
  portal.send(302, "text/plain", "");
}

static void handleStatus() {
  String j = "{";
  j += "\"wifi\":" + String(WiFi.status() == WL_CONNECTED ? "true" : "false") + ",";
  j += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  j += "\"ap\":" + String(apMode ? "true" : "false") + ",";
  j += "\"pet_ok\":" + String(petOk ? "true" : "false") + ",";
  j += "\"pet_host\":\"" + petHost + "\",";
  j += "\"pet_port\":" + String(petPort) + ",";
  j += "\"stretch\":\"" + String(NAME_BY_IDX[stretchIdx]) + "\",";
  j += "\"pins\":{";
  for (int i = 0; i < 4; i++) {
    if (i) j += ",";
    j += "\"" + String(NAME_BY_IDX[i]) + "\":" + String(PIN_BY_NAME[i]);
  }
  j += "}}";
  portal.send(200, "application/json", j);
}

static void handleStretch() {
  pulsePin(stretchIdx);
  relay.send(200, "application/json",
             String("{\"ok\":true,\"action\":\"stretch_pulse\",\"pin\":\"") + NAME_BY_IDX[stretchIdx] +
                 "\",\"ms\":" + String(pulseMs) + "}");
}

static void handleStretchPortal() {
  /* 配网页上的「点动一次」走 80 端口，避免跨端口麻烦 */
  pulsePin(stretchIdx);
  portal.send(200, "application/json", "{\"ok\":true,\"action\":\"stretch_pulse\"}");
}

static void handleCaptive() {
  portal.sendHeader("Location", String("http://") + WiFi.softAPIP().toString() + "/", true);
  portal.send(302, "text/plain", "");
}

static void setupServers() {
  portal.on("/", HTTP_GET, handleRoot);
  portal.on("/save", HTTP_POST, handleSave);
  portal.on("/api/pin", HTTP_POST, handlePin);
  portal.on("/api/pin", HTTP_GET, handlePin);
  portal.on("/api/recheck", HTTP_POST, handleRecheck);
  portal.on("/api/status", HTTP_GET, handleStatus);
  portal.on("/stretch", HTTP_POST, handleStretchPortal);
  portal.on("/stretch", HTTP_GET, handleStretchPortal);
  portal.on("/generate_204", HTTP_GET, handleCaptive);
  portal.on("/hotspot-detect.html", HTTP_GET, handleCaptive);
  portal.onNotFound(handleRoot);
  portal.begin();

  relay.on("/stretch", HTTP_POST, handleStretch);
  relay.on("/stretch", HTTP_GET, handleStretch);
  relay.on("/", HTTP_GET, []() {
    relay.send(200, "application/json",
               "{\"ok\":true,\"service\":\"lumia-chair-relay\",\"stretch\":\"/stretch\"}");
  });
  relay.begin();
}

void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n======= Lumia Chair Relay / LiberNovo =======");
  Serial.printf("pins A7=%d A6=%d A5=%d A4=%d\n", PIN_A7, PIN_A6, PIN_A5, PIN_A4);

  allFloat();
  loadPrefs();
  setupServers();

  bool wifiOk = connectWifi();
  if (wifiOk) {
    checkPet();
    MDNS.begin("libernovo-chair");
  }

  if (!wifiOk || !petOk) {
    startAp();
  }

  Serial.printf("portal http://%s/  relay :8790/stretch  ap=%d pet=%d\n",
                wifiOk ? WiFi.localIP().toString().c_str() : "192.168.4.1", apMode, petOk);
}

void loop() {
  if (apMode) dns.processNextRequest();
  portal.handleClient();
  relay.handleClient();

  uint32_t now = millis();

  /* 周期探活：坏了开 AP，好了关 AP */
  if (now - lastPetCheckMs > 15000) {
    lastPetCheckMs = now;
    if (WiFi.status() == WL_CONNECTED) {
      bool ok = checkPet();
      if (ok) stopAp();
      else startAp();
    } else {
      petOk = false;
      startAp();
    }
  }

  /* STA 掉线则重试 */
  if (WiFi.status() != WL_CONNECTED && !wifiSsid.isEmpty() && now - lastWifiAttemptMs > 20000) {
    lastWifiAttemptMs = now;
    connectWifi(8000);
  }
}
