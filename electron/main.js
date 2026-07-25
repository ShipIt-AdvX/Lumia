'use strict';
const { app, Tray, Menu, BrowserWindow, ipcMain, screen, nativeImage, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

const CFG = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf-8'));
const BASE = CFG.backend.baseUrl;
const FLOAT = CFG.float;
const FRAME_MS = Math.max(1, Math.round(1000 / (FLOAT.fps || 120)));

let tray = null;
let backendProc = null;
let lastEventId = 0;
let popupSeq = 0;

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
  backendProc = spawn(CFG.backend.command, CFG.backend.args, { cwd, stdio: 'ignore', windowsHide: true });
  backendProc.on('error', (err) => console.error('backend spawn failed:', err.message));
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
function retractedX() {
  const wa = workArea();
  return wa.x + wa.width - FLOAT.handleWidth;
}
const TITLE_H = FLOAT.titleHeight || 40;
function effHeight(st) {
  return st.collapsed ? TITLE_H : st.height;
}
function floatX(st) {
  return st.retracted ? retractedX() : targetX(st.width);
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
    if (st.retractTimer) { clearTimeout(st.retractTimer); st.retractTimer = null; }
    if (st.retracted) { st.retracted = false; win.webContents.send('retracted-changed', false); }
    animateBounds(win, { x: wa.x, y: wa.y, width: wa.width, height: wa.height }, FLOAT.animMs);
    win.moveTop();
    win.webContents.send('fullscreen-changed', true);
  } else {
    st.isFullscreen = false;
    st.collapsed = false;
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
    if (!st.noRetract) collapseSiblings(win);
    reflow(true);
    win.moveTop();
    scheduleRetract(win);
  });

  win.on('closed', () => {
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

function closeFloat(win) {
  const st = floatState.get(win.id);
  if (!st || win.isDestroyed()) { if (win && !win.isDestroyed()) win.close(); return; }
  if (st.closing) return;
  st.closing = true;
  if (st.retractTimer) { clearTimeout(st.retractTimer); st.retractTimer = null; }
  if (st.dragTimer) { clearInterval(st.dragTimer); st.dragTimer = null; }
  const wa = workArea();
  const b = win.getBounds();
  animateBounds(win, { x: wa.x + wa.width, y: b.y, width: b.width, height: b.height }, FLOAT.animMs, () => {
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

async function initLastEventId() {
  try { lastEventId = (await api('/api/state')).latest_event_id || 0; } catch (_) {}
}
async function pollEvents() {
  try {
    const data = await api('/api/events/poll?after=' + lastEventId);
    for (const event of data.events || []) {
      lastEventId = Math.max(lastEventId, event.id);
      enqueuePopup(event);
    }
  } catch (_) {}
}

async function requestDelay() {
  try {
    const r = await api('/api/coding/delay', { method: 'POST' });
    if (r && r.ok) { await pollEvents(); }
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
  if (w && floatState.has(w.id)) closeFloat(w);
  if (id === 'delay') { await requestDelay(); }
  else if (id === 'achievements') { openAchievements(); }
});
ipcMain.on('popup-dismiss', (evt) => {
  const w = BrowserWindow.fromWebContents(evt.sender);
  if (w && floatState.has(w.id)) closeFloat(w);
});
ipcMain.on('dev-emit', (_e, event) => { if (event) enqueuePopup(event); });
ipcMain.on('dev-open', (_e, key) => {
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
  if (st.dragTimer) { clearInterval(st.dragTimer); st.dragTimer = null; }
});
ipcMain.on('float-collapse-toggle', (e) => {
  const w = BrowserWindow.fromWebContents(e.sender);
  if (!w || !floatState.has(w.id)) return;
  const st = floatState.get(w.id);
  if (st.noRetract || st.isFullscreen || st.closing) return;
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
ipcMain.on('win-close', (e) => { const w = BrowserWindow.fromWebContents(e.sender); if (!w) return; if (floatState.has(w.id)) closeFloat(w); else w.close(); });
ipcMain.on('win-toggle-fullscreen', (e) => {
  const w = BrowserWindow.fromWebContents(e.sender);
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
  maybeSpawnBackend();
  buildTray();
  initLastEventId().then(() => setInterval(pollEvents, CFG.pollIntervalMs));
});

app.on('window-all-closed', () => {});
app.on('before-quit', () => { if (backendProc && !backendProc.killed) { try { backendProc.kill(); } catch (_) {} } });
