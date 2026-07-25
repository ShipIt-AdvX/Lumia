'use strict';
const { app, Tray, Menu, BrowserWindow, ipcMain, screen, nativeImage, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

const CFG = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf-8'));
const BASE = CFG.backend.baseUrl;
const FLOAT = CFG.float;
const FRAME_MS = Math.max(1, Math.round(1000 / (FLOAT.fps || 120)));

const LOG_FILE = path.join(app.getPath('userData'), 'lumia.log');
function log(scope, msg, extra) {
  const line = '[' + new Date().toISOString() + '] [' + scope + '] ' + msg
    + (extra === undefined ? '' : ' ' + JSON.stringify(extra));
  console.log(line);
  try { fs.appendFileSync(LOG_FILE, line + '\n'); } catch (_) {}
}
function winTag(win) {
  if (!win || win.isDestroyed()) return 'win#gone';
  const st = floatState.get(win.id);
  return (st ? st.key : 'win') + '#' + win.id;
}

if (process.platform === 'linux') {
  app.commandLine.appendSwitch('ozone-platform-hint', 'x11');
  app.commandLine.appendSwitch('enable-transparent-visuals');
  app.disableHardwareAcceleration();
  log('app', 'linux switches applied', { ozone: 'x11', transparentVisuals: true, hwAccel: false });
}

let tray = null;
let backendProc = null;
let lastEventId = 0;
let popupSeq = 0;
let delayWidget = null;
let lockdownWin = null;
let lockdownTimer = null;
let lockdownState = null;
let lockdownSuppressed = false;
let pollFailing = false;

const floats = [];
const floatState = new Map();

function makeTrayIcon() {
  const size = 16;
  const buf = Buffer.alloc(size * size * 4);
  const cx = 7.5, cy = 7.5, r = 7;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = (y * size + x) * 4;
      const inside = (x - cx) ** 2 + (y - cy) ** 2 <= r * r;
      buf[i] = inside ? 0xE0 : 0;
      buf[i + 1] = inside ? 0x88 : 0;
      buf[i + 2] = inside ? 0x7A : 0;
      buf[i + 3] = inside ? 0xFF : 0;
    }
  }
  return nativeImage.createFromBitmap(buf, { width: size, height: size });
}

function maybeSpawnBackend() {
  if (!CFG.backend.spawn) return;
  const cwd = path.resolve(__dirname, CFG.backend.cwd);
  log('backend', 'spawning', { command: CFG.backend.command, args: CFG.backend.args, cwd });
  backendProc = spawn(CFG.backend.command, CFG.backend.args, { cwd, stdio: 'ignore', windowsHide: true });
  backendProc.on('error', (err) => log('backend', 'spawn failed: ' + err.message));
  backendProc.on('exit', (code, signal) => log('backend', 'exited', { code, signal }));
}

async function api(pathname, options) {
  const res = await fetch(BASE + pathname, options);
  return res.json();
}

function workArea() {
  return screen.getPrimaryDisplay().workArea;
}
function targetX(width) {
  const wa = workArea();
  return wa.x + wa.width - FLOAT.marginRight - width;
}
function retractedX(st) {
  const wa = workArea();
  if (st.side === 'left') return wa.x + FLOAT.handleWidth - st.width;
  return wa.x + wa.width - FLOAT.handleWidth;
}
const TITLE_H = FLOAT.titleHeight || 40;
function effHeight(st) {
  return st.collapsed ? TITLE_H : st.height;
}
function floatX(st) {
  if (st.retracted) return retractedX(st);
  if (st.side === 'left') return workArea().x + FLOAT.marginRight;
  return targetX(st.width);
}
function slotY(index) {
  const wa = workArea();
  let y = wa.y + FLOAT.marginTop;
  for (let i = 0; i < index; i++) {
    y += effHeight(floatState.get(floats[i].id)) + FLOAT.gap;
  }
  return y;
}

function animateBounds(win, to, ms, done) {
  const st = floatState.get(win.id);
  if (!st || win.isDestroyed()) return;
  if (st.animTimer) { clearInterval(st.animTimer); st.animTimer = null; }
  const from = win.getBounds();
  if (ms <= 0) { win.setBounds(to); if (done) done(); return; }
  const start = Date.now();
  st.animTimer = setInterval(() => {
    if (win.isDestroyed()) { clearInterval(st.animTimer); return; }
    const t = Math.min(1, (Date.now() - start) / ms);
    const e = 1 - Math.pow(1 - t, 3);
    win.setBounds({
      x: Math.round(from.x + (to.x - from.x) * e),
      y: Math.round(from.y + (to.y - from.y) * e),
      width: Math.round(from.width + (to.width - from.width) * e),
      height: Math.round(from.height + (to.height - from.height) * e),
    });
    if (t >= 1) { clearInterval(st.animTimer); st.animTimer = null; if (done) done(); }
  }, FRAME_MS);
}

function reflow(animate = true) {
  floats.forEach((win, i) => {
    const st = floatState.get(win.id);
    if (!st || st.isFullscreen || st.closing) return;
    st.slotYCache = slotY(i);
    animateBounds(win, { x: floatX(st), y: st.slotYCache, width: st.width, height: effHeight(st) }, animate ? FLOAT.animMs : 0);
  });
}

function collapseSiblings(win) {
  let changed = false;
  floats.forEach((w) => {
    const s = floatState.get(w.id);
    if (!s || s.noRetract) return;
    const want = (w !== win);
    if (s.collapsed !== want) { s.collapsed = want; changed = true; }
  });
  return changed;
}

function setRetracted(win, retracted) {
  const st = floatState.get(win.id);
  if (!st || st.isFullscreen || st.closing || st.retracted === retracted) return;
  st.retracted = retracted;
  log('float', winTag(win) + (retracted ? ' retract' : ' expand'), { side: st.side || 'right' });
  animateBounds(win, { x: floatX(st), y: st.slotYCache, width: st.width, height: effHeight(st) }, FLOAT.animMs);
  win.webContents.send('retracted-changed', retracted);
}

function scheduleRetract(win) {
  const st = floatState.get(win.id);
  if (!st || st.noRetract || st.detached) return;
  if (st.retractTimer) { clearTimeout(st.retractTimer); st.retractTimer = null; }
  if (st.hovering || st.isFullscreen || st.closing) return;
  st.retractTimer = setTimeout(() => {
    st.retractTimer = null;
    setRetracted(win, true);
  }, FLOAT.autoRetractMs);
}

function activateFloat(win) {
  const st = floatState.get(win.id);
  if (!st || st.isFullscreen || st.closing) return;
  win.moveTop();
  if (st.detached) return;
  setRetracted(win, false);
  scheduleRetract(win);
  if (!st.noRetract && collapseSiblings(win)) reflow(true);
}

function detachFloat(win) {
  const st = floatState.get(win.id);
  if (!st || st.detached) return;
  st.detached = true;
  log('float', winTag(win) + ' detached');
  if (st.retractTimer) { clearTimeout(st.retractTimer); st.retractTimer = null; }
  if (st.retracted) { st.retracted = false; win.webContents.send('retracted-changed', false); }
  const idx = floats.indexOf(win);
  if (idx >= 0) floats.splice(idx, 1);
  reflow(true);
}

function toggleFullscreen(win) {
  const st = floatState.get(win.id);
  if (!st) return;
  const wa = workArea();
  if (!st.isFullscreen) {
    st.isFullscreen = true;
    st.fsRestore = win.getBounds();
    log('float', winTag(win) + ' fullscreen enter');
    if (st.retractTimer) { clearTimeout(st.retractTimer); st.retractTimer = null; }
    if (st.retracted) { st.retracted = false; win.webContents.send('retracted-changed', false); }
    animateBounds(win, { x: wa.x, y: wa.y, width: wa.width, height: wa.height }, FLOAT.animMs);
    win.moveTop();
    win.webContents.send('fullscreen-changed', true);
  } else {
    st.isFullscreen = false;
    st.collapsed = false;
    log('float', winTag(win) + ' fullscreen exit', { detached: st.detached });
    win.webContents.send('fullscreen-changed', false);
    if (st.detached) {
      const back = st.fsRestore || win.getBounds();
      animateBounds(win, { x: back.x, y: back.y, width: st.width, height: st.height }, FLOAT.animMs);
    } else {
      collapseSiblings(win);
      reflow(true);
      scheduleRetract(win);
    }
  }
}

function createFloat({ key, file, width, height, title, noRetract, event }) {
  const existing = floats.find((w) => floatState.get(w.id).key === key);
  if (existing) {
    log('float', winTag(existing) + ' reuse existing, activate');
    activateFloat(existing);
    return existing;
  }
  const win = new BrowserWindow({
    width, height,
    frame: false,
    transparent: true,
    resizable: false,
    maximizable: false,
    minimizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
      additionalArguments: ['--lumia-base=' + BASE, '--lumia-title=' + title],
    },
  });
  const st = {
    win, key, width, height,
    isFullscreen: false, collapsed: false,
    retracted: false, hovering: false, retractTimer: null,
    detached: false, dragTimer: null,
    animTimer: null, slotYCache: 0,
    noRetract: !!noRetract,
  };
  floatState.set(win.id, st);
  floats.unshift(win);
  log('float', winTag(win) + ' created', { file, width, height, noRetract: !!noRetract, eventType: event ? event.type : null });
  attachWebContentsLog(win);
  win.setAlwaysOnTop(true, 'floating');
  win.loadFile(path.join(__dirname, file));

  if (event) {
    const sendEvent = () => { if (!win.isDestroyed()) win.webContents.send('show-event', event); };
    if (win.webContents.isLoading()) win.webContents.once('did-finish-load', sendEvent);
    else sendEvent();
  }

  const wa = workArea();
  win.once('ready-to-show', () => {
    win.setBounds({ x: wa.x + wa.width, y: slotY(0), width, height });
    win.showInactive();
    log('float', winTag(win) + ' ready-to-show, shown', win.getBounds());
    if (!st.noRetract) collapseSiblings(win);
    reflow(true);
    win.moveTop();
    scheduleRetract(win);
  });

  win.on('closed', () => {
    log('float', st.key + '#' + win.id + ' closed');
    const idx = floats.indexOf(win);
    if (idx >= 0) floats.splice(idx, 1);
    if (st.animTimer) clearInterval(st.animTimer);
    if (st.retractTimer) clearTimeout(st.retractTimer);
    if (st.dragTimer) clearInterval(st.dragTimer);
    floatState.delete(win.id);
    reflow(true);
  });
  return win;
}

function attachWebContentsLog(win) {
  const tag = () => winTag(win);
  win.webContents.on('did-fail-load', (_e, code, desc, url) => log('wc', tag() + ' did-fail-load', { code, desc, url }));
  win.webContents.on('render-process-gone', (_e, details) => log('wc', tag() + ' render-process-gone', details));
  win.on('unresponsive', () => log('wc', tag() + ' unresponsive'));
  win.on('responsive', () => log('wc', tag() + ' responsive again'));
}

function closeFloat(win) {
  const st = floatState.get(win.id);
  if (!st || win.isDestroyed()) { if (win && !win.isDestroyed()) win.close(); return; }
  if (st.closing) return;
  st.closing = true;
  log('float', winTag(win) + ' closing (slide out)');
  if (st.retractTimer) { clearTimeout(st.retractTimer); st.retractTimer = null; }
  if (st.dragTimer) { clearInterval(st.dragTimer); st.dragTimer = null; }
  const wa = workArea();
  const b = win.getBounds();
  const exitX = st.side === 'left' ? wa.x - b.width : wa.x + wa.width;
  animateBounds(win, { x: exitX, y: b.y, width: b.width, height: b.height }, FLOAT.animMs, () => {
    if (!win.isDestroyed()) win.close();
  });
}

function openSettings() {
  createFloat({ key: 'settings', file: 'settings.html', width: 720, height: 560, title: 'Lumia · 设置' });
}
function openAchievements() {
  createFloat({ key: 'achievements', file: 'achievements.html', width: 560, height: 600, title: 'Lumia · 今日成就墙' });
}
function openDevPanel() {
  createFloat({ key: 'dev', file: 'dev.html', width: 300, height: 460, title: 'Lumia · 开发者面板', noRetract: true });
}

function enqueuePopup(event) {
  createFloat({
    key: 'popup-' + (++popupSeq),
    file: 'popup.html',
    width: CFG.popup.width,
    height: CFG.popup.height,
    title: event.title || 'Lumia',
    noRetract: true,
    event,
  });
}

function shutdownNow() {
  log('lockdown', 'executing system shutdown', { platform: process.platform });
  if (process.platform === 'win32') spawn('shutdown', ['/s', '/t', '0']);
  else if (process.platform === 'linux') spawn('shutdown', ['-h', 'now']);
}

function closeLockdown() {
  if (!lockdownWin && !lockdownTimer) return;
  log('lockdown', 'closing, shutdown cancelled');
  if (lockdownTimer) { clearInterval(lockdownTimer); lockdownTimer = null; }
  lockdownState = null;
  if (lockdownWin && !lockdownWin.isDestroyed()) lockdownWin.destroy();
  lockdownWin = null;
}

async function openLockdown(kind) {
  if (lockdownWin && !lockdownWin.isDestroyed()) {
    if (lockdownState && kind === 'locked') { lockdownState.kind = 'locked'; lockdownState.delayAvailable = false; }
    log('lockdown', 'already open, state escalated', { kind });
    lockdownWin.moveTop();
    return;
  }
  let delayAvailable = false;
  let minutes = 0;
  if (kind === 'limit') {
    try {
      const s = await api('/api/state');
      const d = (s.coding && s.coding.delay) || {};
      delayAvailable = !!d.available;
      minutes = d.minutes || 0;
    } catch (_) {}
  }
  const win = new BrowserWindow({
    fullscreen: true,
    frame: false,
    resizable: false,
    minimizable: false,
    closable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    backgroundColor: '#141318',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
      additionalArguments: ['--lumia-base=' + BASE, '--lumia-title=开发时长已用尽'],
    },
  });
  lockdownWin = win;
  lockdownState = { kind, delayAvailable, minutes, remaining: 60 };
  log('lockdown', 'created win#' + win.id, lockdownState);
  attachWebContentsLog(win);
  win.setAlwaysOnTop(true, 'screen-saver');
  win.on('close', (e) => e.preventDefault());
  win.loadFile(path.join(__dirname, 'lockdown.html'));
  const sendState = () => {
    if (win.isDestroyed() || !lockdownState) return;
    win.webContents.send('show-event', { type: 'lockdown', ...lockdownState });
  };
  win.once('ready-to-show', () => {
    win.show();
    win.focus();
    log('lockdown', 'shown, countdown started', { remaining: lockdownState.remaining });
    sendState();
    lockdownTimer = setInterval(() => {
      if (!lockdownState) return;
      lockdownState.remaining -= 1;
      sendState();
      if (lockdownState.remaining <= 0) {
        clearInterval(lockdownTimer);
        lockdownTimer = null;
        log('lockdown', 'countdown reached zero');
        shutdownNow();
      }
    }, 1000);
  });
  win.on('closed', () => {
    log('lockdown', 'win#' + win.id + ' closed');
    if (lockdownTimer) { clearInterval(lockdownTimer); lockdownTimer = null; }
    if (lockdownWin === win) { lockdownWin = null; lockdownState = null; }
  });
}

function openDelayWidget(endsAt, minutes) {
  if (delayWidget && !delayWidget.isDestroyed()) { log('delay-widget', 'already open, moveTop'); delayWidget.moveTop(); return; }
  const width = CFG.popup.width, height = CFG.popup.height;
  const win = new BrowserWindow({
    width, height,
    frame: false,
    transparent: true,
    resizable: false,
    maximizable: false,
    minimizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    show: false,
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
      additionalArguments: ['--lumia-base=' + BASE, '--lumia-title=延时'],
    },
  });
  delayWidget = win;
  const st = {
    win, key: 'delay-widget', width, height,
    isFullscreen: false, collapsed: false,
    retracted: false, hovering: false, retractTimer: null,
    detached: false, dragTimer: null,
    animTimer: null, slotYCache: 0,
    noRetract: false, side: 'left',
  };
  floatState.set(win.id, st);
  win.setAlwaysOnTop(true, 'floating');
  log('delay-widget', 'created win#' + win.id, { endsAt, minutes });
  attachWebContentsLog(win);
  win.loadFile(path.join(__dirname, 'delay.html'));

  const sendCard = (title, message) => {
    if (!win.isDestroyed()) win.webContents.send('show-event', { type: 'delay_widget', title, message, actions: [] });
  };
  const fmtRemaining = () => {
    const left = Math.max(0, Math.round((new Date(endsAt) - Date.now()) / 1000));
    const mm = String(Math.floor(left / 60)).padStart(2, '0');
    const ss = String(left % 60).padStart(2, '0');
    return { left, text: mm + ':' + ss };
  };
  const phaseTimers = [];
  let tickTimer = null;

  win.once('ready-to-show', () => {
    const wa = workArea();
    const cx = wa.x + Math.round((wa.width - width) / 2);
    const cy = wa.y + Math.round((wa.height - height) / 2);
    st.slotYCache = cy;
    sendCard('延时已开启', `最后 ${minutes} 分钟，用完今天就真的下班了。`);
    win.setBounds({ x: wa.x + wa.width, y: cy, width, height });
    win.showInactive();
    animateBounds(win, { x: cx, y: cy, width, height }, FLOAT.animMs);

    phaseTimers.push(setTimeout(() => {
      const r = fmtRemaining();
      sendCard('延时剩余', r.text);
      tickTimer = setInterval(() => {
        const t = fmtRemaining();
        sendCard('延时剩余', t.text);
        if (t.left <= 0) { clearInterval(tickTimer); tickTimer = null; log('delay-widget', 'countdown finished, closing'); closeFloat(win); }
      }, 1000);
    }, 2000));

    phaseTimers.push(setTimeout(() => {
      animateBounds(win, { x: floatX(st), y: cy, width, height }, FLOAT.animMs);
      scheduleRetract(win);
    }, 4000));
  });

  win.on('closed', () => {
    log('delay-widget', 'win#' + win.id + ' closed');
    phaseTimers.forEach(clearTimeout);
    if (tickTimer) clearInterval(tickTimer);
    if (st.animTimer) clearInterval(st.animTimer);
    if (st.retractTimer) clearTimeout(st.retractTimer);
    floatState.delete(win.id);
    if (delayWidget === win) delayWidget = null;
  });
}

async function initLastEventId() {
  try { lastEventId = (await api('/api/state')).latest_event_id || 0; } catch (_) {}
}
async function pollEvents() {
  try {
    const data = await api('/api/events/poll?after=' + lastEventId);
    if (pollFailing) { pollFailing = false; log('poll', 'backend reachable again'); }
    if (typeof data.latest_id === 'number' && data.latest_id < lastEventId) {
      log('poll', 'event watermark rewound, backend restarted', { from: lastEventId, to: data.latest_id });
      lastEventId = data.latest_id;
      return;
    }
    for (const event of data.events || []) {
      lastEventId = Math.max(lastEventId, event.id);
      log('poll', 'event received', { id: event.id, type: event.type, title: event.title });
      if (event.type === 'coding_delay_started' && delayWidget && !delayWidget.isDestroyed()) { log('poll', 'skip coding_delay_started, delay widget alive'); continue; }
      if (event.type === 'coding_limit') { lockdownSuppressed = false; await openLockdown('limit'); continue; }
      if (event.type === 'coding_locked') { lockdownSuppressed = false; await openLockdown('locked'); continue; }
      enqueuePopup(event);
    }
  } catch (err) {
    if (!pollFailing) { pollFailing = true; log('poll', 'backend unreachable: ' + err.message); }
  }
}

async function checkLockdownState() {
  try {
    const s = await api('/api/state');
    const st = (s.coding && s.coding.state) || '';
    if (st !== 'limit_reached' && st !== 'day_locked') { lockdownSuppressed = false; return; }
    if (lockdownSuppressed) return;
    if (delayWidget && !delayWidget.isDestroyed()) return;
    if (lockdownWin && !lockdownWin.isDestroyed()) return;
    log('lockdown', 'level check triggered', { state: st });
    await openLockdown(st === 'limit_reached' ? 'limit' : 'locked');
  } catch (_) {}
}

async function requestDelay() {
  try {
    const s = await api('/api/state');
    const d = (s.coding && s.coding.delay) || {};
    if (d.available) {
      enqueuePopup({
        type: 'delay_confirm', title: '开启延时？',
        message: `本次开发时间将延长 ${d.minutes} 分钟，到时后无法再继续开发. 确定开启吗?`,
        actions: [{ id: 'delay-confirm', label: '开启延时' }],
        dismissLabel: '再想想',
      });
    } else {
      enqueuePopup({
        type: 'delay_feedback', title: '延时不可用',
        message: d.used_today ? '今天已经用过延时了' : '延时当前不可用', actions: [],
      });
    }
  } catch (_) {
    enqueuePopup({
      type: 'delay_feedback', title: '延时失败',
      message: '后端未连接', actions: [],
    });
  }
}

async function confirmDelay() {
  try {
    const r = await api('/api/coding/delay', { method: 'POST' });
    if (r && r.ok) { openDelayWidget(r.ends_at, r.minutes); }
    else {
      enqueuePopup({
        type: 'delay_feedback', title: '延时失败',
        message: (r && r.reason) || '后端拒绝了这次延时请求', actions: [],
      });
    }
  } catch (_) {
    enqueuePopup({
      type: 'delay_feedback', title: '延时失败',
      message: '后端未连接', actions: [],
    });
  }
}

ipcMain.on('popup-action', async (evt, payload) => {
  const id = payload && payload.id;
  const w = BrowserWindow.fromWebContents(evt.sender);
  log('ipc', 'popup-action from ' + winTag(w), { id });
  if (w && floatState.has(w.id)) closeFloat(w);
  if (id === 'delay') { await requestDelay(); }
  else if (id === 'delay-confirm') { await confirmDelay(); }
  else if (id === 'achievements') { openAchievements(); }
});
ipcMain.on('popup-dismiss', (evt) => {
  const w = BrowserWindow.fromWebContents(evt.sender);
  log('ipc', 'popup-dismiss from ' + winTag(w));
  if (w && floatState.has(w.id)) closeFloat(w);
});
ipcMain.on('dev-emit', (_e, event) => {
  if (!event) return;
  log('ipc', 'dev-emit', { type: event.type });
  if (event.type === 'coding_limit') { openLockdown('limit'); return; }
  if (event.type === 'coding_locked') { openLockdown('locked'); return; }
  enqueuePopup(event);
});
ipcMain.on('lockdown-cancel', () => { log('ipc', 'lockdown-cancel'); lockdownSuppressed = true; closeLockdown(); });
ipcMain.handle('lockdown-delay', async () => {
  log('ipc', 'lockdown-delay requested');
  try {
    const r = await api('/api/coding/delay', { method: 'POST' });
    if (r && r.ok) {
      log('lockdown', 'delay granted', { minutes: r.minutes, endsAt: r.ends_at });
      closeLockdown();
      openDelayWidget(r.ends_at, r.minutes);
      return { ok: true };
    }
    log('lockdown', 'delay rejected', { reason: r && r.reason });
    return { ok: false, reason: (r && r.reason) || '后端拒绝了这次延时请求' };
  } catch (_) {
    log('lockdown', 'delay failed: backend unreachable');
    return { ok: false, reason: '后端未连接' };
  }
});
ipcMain.on('dev-open', (_e, key) => {
  log('ipc', 'dev-open', { key });
  if (key === 'settings') openSettings();
  else if (key === 'achievements') openAchievements();
});

ipcMain.on('float-hover', (e, hovering) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  if (!w || !floatState.has(w.id)) return;
  const st = floatState.get(w.id);
  st.hovering = !!hovering;
  if (hovering) {
    if (st.retractTimer) { clearTimeout(st.retractTimer); st.retractTimer = null; }
    setRetracted(w, false);
  } else {
    scheduleRetract(w);
  }
});
ipcMain.on('float-drag-start', (e) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  if (!w || !floatState.has(w.id)) return;
  const st = floatState.get(w.id);
  if (st.isFullscreen || st.closing) return;
  log('ipc', 'float-drag-start ' + winTag(w));
  detachFloat(w);
  if (st.animTimer) { clearInterval(st.animTimer); st.animTimer = null; }
  if (st.dragTimer) { clearInterval(st.dragTimer); st.dragTimer = null; }
  const cursorStart = screen.getCursorScreenPoint();
  const boundsStart = w.getBounds();
  st.dragTimer = setInterval(() => {
    if (w.isDestroyed()) { clearInterval(st.dragTimer); return; }
    const c = screen.getCursorScreenPoint();
    w.setPosition(boundsStart.x + (c.x - cursorStart.x), boundsStart.y + (c.y - cursorStart.y));
  }, FRAME_MS);
});
ipcMain.on('float-drag-end', (e) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  if (!w || !floatState.has(w.id)) return;
  const st = floatState.get(w.id);
  if (st.dragTimer) { clearInterval(st.dragTimer); st.dragTimer = null; log('ipc', 'float-drag-end ' + winTag(w), w.getBounds()); }
});
ipcMain.on('float-collapse-toggle', (e) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  if (!w || !floatState.has(w.id)) return;
  const st = floatState.get(w.id);
  if (st.noRetract || st.isFullscreen || st.closing) return;
  log('ipc', 'float-collapse-toggle ' + winTag(w), { collapsed: st.collapsed, detached: st.detached });
  if (st.detached) {
    st.collapsed = !st.collapsed;
    const b = w.getBounds();
    animateBounds(w, { x: b.x, y: b.y, width: st.width, height: effHeight(st) }, FLOAT.animMs);
    return;
  }
  if (st.collapsed) {
    w.moveTop();
    collapseSiblings(w);
  } else {
    st.collapsed = true;
  }
  reflow(true);
});
ipcMain.on('win-close', (e) => { const w = BrowserWindow.fromWebContents(e.sender); if (!w) return; if (delayWidget && w === delayWidget) { log('ipc', 'win-close blocked for delay widget'); return; } log('ipc', 'win-close ' + winTag(w)); if (floatState.has(w.id)) closeFloat(w); else w.close(); });
ipcMain.on('win-toggle-fullscreen', (e) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  log('ipc', 'win-toggle-fullscreen ' + winTag(w));
  if (w && floatState.has(w.id)) toggleFullscreen(w);
});
ipcMain.handle('pick-directory', async (e) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  const r = await dialog.showOpenDialog(w, { properties: ['openDirectory'] });
  return r.canceled ? null : r.filePaths[0];
});

function buildTray() {
  tray = new Tray(makeTrayIcon());
  tray.setToolTip('Lumia · 不要在我面前猝死');
  const menu = Menu.buildFromTemplate([
    { label: '查看今日状态', click: showStatusPopup },
    { label: '成就墙', click: openAchievements },
    { label: '请求延时', click: requestDelay },
    { type: 'separator' },
    { label: '设置…', click: openSettings },
    { label: '开发者面板', click: openDevPanel },
    { type: 'separator' },
    { label: '退出 Lumia', click: () => app.quit() },
  ]);
  tray.setContextMenu(menu);
  tray.on('click', showStatusPopup);
}

async function showStatusPopup() {
  try {
    const s = await api('/api/state');
    const c = s.coding;
    const mins = (sec) => Math.round((sec || 0) / 60);
    enqueuePopup({
      type: 'status', title: 'Lumia 今日状态',
      message: `已开发 ${mins(c.used_seconds)} / ${mins(c.allowed_effective_seconds)} 分钟 · 状态 ${c.state}`,
      actions: [{ id: 'achievements', label: '成就墙' }],
    });
  } catch (_) {
    enqueuePopup({ type: 'status', title: 'Lumia', message: '后端未连接（:8787）。', actions: [] });
  }
}

app.whenReady().then(() => {
  log('app', 'ready', {
    platform: process.platform,
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    logFile: LOG_FILE,
  });
  maybeSpawnBackend();
  buildTray();
  initLastEventId().then(() => {
    setInterval(pollEvents, CFG.pollIntervalMs);
    setInterval(checkLockdownState, 5000);
  });
});

app.on('window-all-closed', () => {});
app.on('before-quit', () => {
  log('app', 'before-quit');
  closeLockdown();
  if (backendProc && !backendProc.killed) { try { backendProc.kill(); } catch (_) {} }
});
