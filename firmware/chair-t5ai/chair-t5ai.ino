/**
 * Lumia Chair Relay — LiberNovo 扶手键并联（涂鸦 T5AI）
 *
 * 引脚：P7 / P6 / P5 / P4 开漏点动（空闲 INPUT，按下 OUTPUT LOW）
 * 配网：读 LittleFS → 连 WiFi → 探桌宠 /api/health →
 *   失败则 SoftAP「LiberNovo」密码 12345678 → http://192.168.4.1/
 * 拉伸：POST/GET :8790/stretch 与 :80/stretch
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

#if __has_include("config.h")
#include "config.h"
#else
#include "config.h.example"
#endif

#ifndef PIN_P7
#define PIN_P7 7
#endif
#ifndef PIN_P6
#define PIN_P6 6
#endif
#ifndef PIN_P5
#define PIN_P5 5
#endif
#ifndef PIN_P4
#define PIN_P4 4
#endif
#ifndef LED_PIN
#define LED_PIN 1
#endif
#ifndef AP_SSID
#define AP_SSID "LiberNovo"
#endif
#ifndef AP_PASS
#define AP_PASS "12345678"
#endif
#ifndef DEFAULT_PET_PORT
#define DEFAULT_PET_PORT 8787
#endif
#ifndef DEFAULT_PULSE_MS
#define DEFAULT_PULSE_MS 300
#endif

static const int PIN_BY_IDX[4] = {PIN_P7, PIN_P6, PIN_P5, PIN_P4};
static const char *const NAME_BY_IDX[4] = {"P7", "P6", "P5", "P4"};

VFSFILE fs(LITTLEFS);
DNSServer dns;
WiFiServer portal;
WiFiServer relay;

String wifiSsid, wifiPass, petHost;
uint16_t petPort = DEFAULT_PET_PORT;
uint16_t pulseMs = DEFAULT_PULSE_MS;
uint8_t stretchIdx = 0;

bool apMode = false;
bool petOk = false;
uint32_t lastPetCheckMs = 0;
uint32_t lastWifiAttemptMs = 0;

static void pinFloat(int gpio) { pinMode(gpio, INPUT); }
static void pinLow(int gpio) {
  pinMode(gpio, OUTPUT);
  digitalWrite(gpio, LOW);
}
static void allFloat() {
  for (int i = 0; i < 4; i++) pinFloat(PIN_BY_IDX[i]);
}

static void pulsePin(int idx) {
  if (idx < 0 || idx > 3) return;
  uint16_t ms = pulseMs;
  if (ms < 80) ms = 80;
  if (ms > 800) ms = 800;
  PR_NOTICE("[pulse] %s gpio=%d %ums", NAME_BY_IDX[idx], PIN_BY_IDX[idx], ms);
  digitalWrite(LED_PIN, HIGH);
  pinLow(PIN_BY_IDX[idx]);
  delay(ms);
  pinFloat(PIN_BY_IDX[idx]);
  digitalWrite(LED_PIN, LOW);
}

static int pinIndexFromName(const String &s) {
  if (s == "P7" || s == "p7" || s == "7" || s == "0") return 0;
  if (s == "P6" || s == "p6" || s == "6" || s == "1") return 1;
  if (s == "P5" || s == "p5" || s == "5" || s == "2") return 2;
  if (s == "P4" || s == "p4" || s == "4" || s == "3") return 3;
  return -1;
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

static void loadPrefs() {
  wifiSsid = "";
  wifiPass = "";
  petHost = "";
  petPort = DEFAULT_PET_PORT;
  pulseMs = DEFAULT_PULSE_MS;
  stretchIdx = 0;
  if (!fs.exist("/chair.cfg")) return;
  TUYA_FILE fd = fs.open("/chair.cfg", "r");
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
      else if (k == "pet_host") petHost = v;
      else if (k == "pet_port") petPort = (uint16_t)v.toInt();
      else if (k == "pulse_ms") pulseMs = (uint16_t)v.toInt();
      else if (k == "stretch_i") stretchIdx = (uint8_t)v.toInt();
    }
    start = nl + 1;
  }
  if (petPort == 0) petPort = DEFAULT_PET_PORT;
  if (stretchIdx > 3) stretchIdx = 0;
  if (pulseMs < 80) pulseMs = 80;
  if (pulseMs > 800) pulseMs = 800;
  PR_NOTICE("prefs ssid=%s pet=%s:%u stretch=%s", wifiSsid.c_str(), petHost.c_str(),
            (unsigned)petPort, NAME_BY_IDX[stretchIdx]);
}

static void savePrefs() {
  String out;
  out += "ssid=" + wifiSsid + "\n";
  out += "pass=" + wifiPass + "\n";
  out += "pet_host=" + petHost + "\n";
  out += "pet_port=" + String(petPort) + "\n";
  out += "pulse_ms=" + String(pulseMs) + "\n";
  out += "stretch_i=" + String(stretchIdx) + "\n";
  TUYA_FILE fd = fs.open("/chair.cfg", "tw");
  if (!fd) {
    PR_ERR("save prefs open fail");
    return;
  }
  fs.write(out.c_str(), (int)out.length(), fd);
  fs.close(fd);
  PR_NOTICE("prefs saved");
}

static bool checkPet() {
  petOk = false;
  if (petHost.isEmpty() || WiFi.status() != WSS_GOT_IP) return false;
  WiFiClient cli;
  cli.setTimeout(2500);
  if (!cli.connect(petHost.c_str(), petPort)) {
    PR_NOTICE("[pet] connect fail %s:%u", petHost.c_str(), (unsigned)petPort);
    return false;
  }
  cli.print(String("GET /api/health HTTP/1.1\r\nHost: ") + petHost + "\r\nConnection: close\r\n\r\n");
  uint32_t t0 = millis();
  String resp;
  while (millis() - t0 < 3000) {
    while (cli.available()) resp += (char)cli.read();
    if (!cli.connected() && !cli.available()) break;
    delay(10);
  }
  cli.stop();
  petOk = resp.indexOf("200") > 0 && resp.indexOf("ok") >= 0;
  PR_NOTICE("[pet] ok=%d", petOk);
  return petOk;
}

static void startAp() {
  if (apMode) return;
  PR_NOTICE("[ap] LiberNovo pass=%s @ 192.168.4.1", AP_PASS);
  /* Tuya：softAP(NULL) 会 strlen(NULL) 崩；SDK 固定 WPA2，必须带 ≥8 位密码 */
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
  /* 配网 AP 已开时保持 AP+STA，避免把 LiberNovo 关掉 */
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

static String pageHtml() {
  String sta = (WiFi.status() == WSS_GOT_IP) ? WiFi.localIP().toString() : "-";
  String h;
  h.reserve(5500);
  h += F("<!DOCTYPE html><html><head><meta charset=utf-8>"
         "<meta name=viewport content=\"width=device-width,initial-scale=1\">"
         "<title>LiberNovo 椅控(T5AI)</title><style>"
         "body{font-family:system-ui,sans-serif;max-width:520px;margin:24px auto;padding:0 16px;"
         "background:#0f1419;color:#e7ecf1}"
         "h1{font-size:1.25rem}h2{font-size:1rem;margin:20px 0 8px;color:#9db0c0}"
         ".card{background:#1a222c;border-radius:12px;padding:16px;margin:12px 0}"
         "label{display:block;font-size:.85rem;color:#9db0c0;margin:10px 0 4px}"
         "input,select{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;"
         "border:1px solid #2c3a48;background:#0f1419;color:#e7ecf1}"
         "button{appearance:none;border:0;border-radius:10px;padding:12px 14px;width:100%;"
         "background:#3d8bfd;color:#fff;font-weight:600;margin-top:8px}"
         "button.ghost{background:#2c3a48}.hold{background:#c27a2c}"
         ".row{display:grid;grid-template-columns:1fr 1fr;gap:8px}"
         ".ok{color:#5ddea6}.bad{color:#ff7b72}.hint{color:#9db0c0;font-size:.85rem}"
         "</style></head><body>");
  h += F("<h1>LiberNovo · 椅控 (T5AI)</h1>");
  h += "<p class=hint>AP <b>http://192.168.4.1/</b> · STA <b>http://" + htmlEscape(sta) +
       "/</b><br>拉伸 <code>:8790/stretch</code> · 脚 P7–P4</p>";
  h += F("<div class=card><h2>状态</h2><p>WiFi：");
  h += (WiFi.status() == WSS_GOT_IP) ? "<span class=ok>已连接 " + htmlEscape(sta) + "</span>"
                                     : "<span class=bad>未连接</span>";
  h += "<br>AP：";
  h += apMode ? "<span class=ok>LiberNovo</span>" : "关闭";
  h += "<br>桌宠：";
  h += petOk ? "<span class=ok>可达</span>" : "<span class=bad>不可达/未配置</span>";
  h += "<br>拉伸脚：<b>" + String(NAME_BY_IDX[stretchIdx]) + "</b> · " + String(pulseMs) + " ms</p>";
  h += F("<form method=POST action=/recheck><button class=ghost type=submit>重新探测桌宠</button></form></div>");

  h += F("<div class=card><h2>配网</h2><form method=POST action=/save>");
  h += "<label>WiFi SSID</label><input name=ssid value=\"" + htmlEscape(wifiSsid) + "\" required>";
  h += "<label>WiFi 密码</label><input name=pass type=password value=\"" + htmlEscape(wifiPass) + "\">";
  h += "<label>桌宠 / OrangePi IP</label><input name=pet_host value=\"" + htmlEscape(petHost) +
       "\" required>";
  h += "<label>端口</label><input name=pet_port type=number value=\"" + String(petPort) + "\">";
  h += "<label>拉伸脚</label><select name=stretch_i>";
  for (int i = 0; i < 4; i++) {
    h += "<option value=" + String(i);
    if (i == stretchIdx) h += " selected";
    h += ">" + String(NAME_BY_IDX[i]) + " (GPIO " + String(PIN_BY_IDX[i]) + ")</option>";
  }
  h += "</select>";
  h += "<label>点动 ms</label><input name=pulse_ms type=number value=\"" + String(pulseMs) + "\">";
  h += F("<button type=submit>保存并应用</button></form></div>");

  h += F("<div class=card><h2>按键测试</h2><p class=hint>按住=拉低；松开=悬空</p><div class=row>");
  for (int i = 0; i < 4; i++) {
    h += "<button type=button class=hold data-pin=\"" + String(NAME_BY_IDX[i]) + "\">按住测 " +
         String(NAME_BY_IDX[i]) + "</button>";
  }
  h += F("</div><button type=button class=ghost id=pulse style=\"margin-top:12px\">点动拉伸脚</button></div>");
  h += F("<script>"
         "async function pin(n,d){fetch('/api/pin?name='+n+'&state='+(d?'low':'float'),{method:'POST'})}"
         "document.querySelectorAll('.hold').forEach(b=>{"
         "const n=b.dataset.pin;"
         "const go=e=>{e.preventDefault();pin(n,true)};"
         "const up=e=>{e.preventDefault();pin(n,false)};"
         "b.addEventListener('mousedown',go);b.addEventListener('mouseup',up);b.addEventListener('mouseleave',up);"
         "b.addEventListener('touchstart',go,{passive:false});b.addEventListener('touchend',up);"
         "});"
         "document.getElementById('pulse').onclick=()=>fetch('/stretch',{method:'POST'});"
         "</script></body></html>");
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
  String query;
  if (q >= 0) {
    query = path.substring(q + 1);
    path = path.substring(0, q);
  }

  if (path == "/save" && isPost) {
    String v;
    v = formGet(body, "ssid");
    if (v.length()) wifiSsid = v;
    v = formGet(body, "pass");
    wifiPass = v;
    v = formGet(body, "pet_host");
    if (v.length()) petHost = v;
    v = formGet(body, "pet_port");
    if (v.length()) petPort = (uint16_t)v.toInt();
    v = formGet(body, "pulse_ms");
    if (v.length()) pulseMs = (uint16_t)v.toInt();
    v = formGet(body, "stretch_i");
    if (v.length()) stretchIdx = (uint8_t)v.toInt();
    if (petPort == 0) petPort = DEFAULT_PET_PORT;
    if (stretchIdx > 3) stretchIdx = 0;
    if (pulseMs < 80) pulseMs = 80;
    if (pulseMs > 800) pulseMs = 800;
    savePrefs();
    sendText(client, 200, "text/html; charset=utf-8",
             F("<!DOCTYPE html><meta charset=utf-8><body style='font-family:sans-serif;padding:24px;"
               "background:#0f1419;color:#e7ecf1'>已保存，正在应用…"
               "<script>setTimeout(()=>location.href='/',1500)</script></body>"));
    client.stop();
    delay(200);
    stopAp();
    bool ok = connectWifi(20000);
    if (ok) checkPet();
    if (!ok || !petOk) startAp();
    return;
  }

  if (path == "/recheck" && isPost) {
    if (WiFi.status() == WSS_GOT_IP) checkPet();
    if (petOk) stopAp();
    else startAp();
    client.println("HTTP/1.1 302 Found");
    client.println("Location: /");
    client.println("Content-Length: 0");
    client.println("Connection: close");
    client.println();
    client.stop();
    return;
  }

  if (path == "/api/pin") {
    String name = formGet(query.length() ? query : body, "name");
    if (!name.length()) {
      int p = query.indexOf("name=");
      if (p >= 0) {
        int e = query.indexOf('&', p);
        name = query.substring(p + 5, e < 0 ? query.length() : e);
      }
    }
    String st = formGet(query.length() ? query : body, "state");
    if (!st.length()) {
      int p = query.indexOf("state=");
      if (p >= 0) {
        int e = query.indexOf('&', p);
        st = query.substring(p + 6, e < 0 ? query.length() : e);
      }
    }
    int idx = pinIndexFromName(name);
    if (idx < 0) {
      sendText(client, 400, "application/json", "{\"ok\":false}");
    } else {
      if (st == "low" || st == "0" || st == "down") pinLow(PIN_BY_IDX[idx]);
      else pinFloat(PIN_BY_IDX[idx]);
      sendText(client, 200, "application/json",
               String("{\"ok\":true,\"pin\":\"") + NAME_BY_IDX[idx] + "\"}");
    }
    client.stop();
    return;
  }

  if (path == "/stretch") {
    pulsePin(stretchIdx);
    sendText(client, 200, "application/json",
             String("{\"ok\":true,\"action\":\"stretch_pulse\",\"pin\":\"") + NAME_BY_IDX[stretchIdx] +
                 "\",\"ms\":" + String(pulseMs) + "}");
    client.stop();
    return;
  }

  if (path == "/api/status") {
    String j = "{";
    j += "\"wifi\":" + String(WiFi.status() == WSS_GOT_IP ? "true" : "false") + ",";
    j += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
    j += "\"ap\":" + String(apMode ? "true" : "false") + ",";
    j += "\"pet_ok\":" + String(petOk ? "true" : "false") + ",";
    j += "\"stretch\":\"" + String(NAME_BY_IDX[stretchIdx]) + "\"";
    j += "}";
    sendText(client, 200, "application/json", j);
    client.stop();
    return;
  }

  // captive / default page
  sendText(client, 200, "text/html; charset=utf-8", pageHtml());
  client.stop();
}

void setup() {
  Serial.begin(115200);
  Log.begin();
  PR_NOTICE("======= Lumia Chair Relay T5AI / LiberNovo =======");

  if (OPRT_OK != board_register_hardware()) {
    PR_ERR("board_register_hardware failed");
  }

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
  allFloat();
  loadPrefs();

  portal.begin(80);
  relay.begin(8790);

  /* 先开配网 AP，再尝试 STA（失败会重建 AP） */
  startAp();

  bool wifiOk = connectWifi(12000);
  if (wifiOk) {
    checkPet();
    if (petOk) stopAp();
  } else if (!apMode) {
    startAp();
  }

  PR_NOTICE("portal :80  relay :8790/stretch  ap=%d pet=%d pins=%d/%d/%d/%d", apMode, petOk, PIN_P7,
            PIN_P6, PIN_P5, PIN_P4);
  lastWifiAttemptMs = millis();
  lastPetCheckMs = millis();
}

void loop() {
  if (apMode) dns.processNextRequest();

  WiFiClient c1 = portal.available();
  if (c1) handleClient(c1);
  WiFiClient c2 = relay.available();
  if (c2) handleClient(c2);

  uint32_t now = millis();
  if (now - lastPetCheckMs > 15000) {
    lastPetCheckMs = now;
    if (WiFi.status() == WSS_GOT_IP) {
      if (checkPet()) {
        if (apMode) stopAp();
      } else {
        startAp();
      }
    } else {
      petOk = false;
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
}
