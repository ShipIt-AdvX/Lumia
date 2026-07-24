'use strict';
/**
 * Lumia desktop shell.
 *
 * Responsibilities:
 *   - live in the system tray (right-click menu -> settings / status / quit),
 *   - poll the Python brain for events and render non-focus-stealing popups,
 *   - never grab the foreground window: popups use showInactive() + focusable:false.
 */
const { app, Tray, Menu, BrowserWindow, ipcMain, screen, nativeImage } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

const CFG = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf-8'));
const BASE = CFG.backend.baseUrl;

let tray = null;
let popupWin = null;
let settingsWin = null;
let achievementsWin = null;
let backendProc = null;
let lastEventId = 0;
const popupQueue = [];
let popupBusy = false;

// ----------------------------------------------------------------------
// Tray icon: build a small BGRA bitmap so we need no binary asset on disk.
// ----------------------------------------------------------------------
function makeTrayIcon() {
  const size = 16;
  const buf = Buffer.alloc(size * size * 4);
  const cx = 7.5, cy = 7.5, r = 7;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = (y * size + x) * 4;
      const inside = (x - cx) ** 2 + (y - cy) ** 2 <= r * r;
      // BGRA, premultiplied. Lumia purple-ish glow.
      buf[i] = inside ? 0xE0 : 0;      // B
      buf[i + 1] = inside ? 0x88 : 0;  // G
      buf[i + 2] = inside ? 0x7A : 0;  // R
      buf[i + 3] = inside ? 0xFF : 0;  // A
    }
  }
  return nativeImage.createFromBitmap(buf, { width: size, height: size });
}

// ----------------------------------------------------------------------
// Backend spawning (optional)
// ----------------------------------------------------------------------
function maybeSpawnBackend() {
  if (!CFG.backend.spawn) return;
  const cwd = path.resolve(__dirname, CFG.backend.cwd);
  backendProc = spawn(CFG.backend.command, CFG.backend.args, {
    cwd,
    stdio: 'ignore',
    windowsHide: true,
  });
  backendProc.on('error', (err) => console.error('backend spawn failed:', err.message));
}

// ----------------------------------------------------------------------
// HTTP helpers (Electron's main process has global fetch)
// ----------------------------------------------------------------------
async function api(pathname, options) {
  const res = await fetch(BASE + pathname, options);
  return res.json();
}

// ----------------------------------------------------------------------
// Popup window (non-focus)
// ----------------------------------------------------------------------
function positionPopup(win) {
  const wa = screen.getPrimaryDisplay().workArea;
  const { width, height, marginRight, marginBottom } = CFG.popup;
  win.setBounds({
    x: wa.x + wa.width - width - marginRight,
    y: wa.y + wa.height - height - marginBottom,
    width,
    height,
  });
}

function ensurePopup() {
  if (popupWin && !popupWin.isDestroyed()) return popupWin;
  popupWin = new BrowserWindow({
    width: CFG.popup.width,
    height: CFG.popup.height,
    show: false,
    frame: false,
    transparent: true,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    focusable: false, // never steal keyboard focus
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: ['--lumia-base=' + BASE],
    },
  });
  popupWin.setAlwaysOnTop(true, 'screen-saver');
  popupWin.setVisibleOnAllWorkspaces(true);
  popupWin.loadFile(path.join(__dirname, 'popup.html'));
  return popupWin;
}

function enqueuePopup(event) {
  popupQueue.push(event);
  drainPopupQueue();
}

function drainPopupQueue() {
  if (popupBusy || popupQueue.length === 0) return;
  const event = popupQueue.shift();
  popupBusy = true;
  const win = ensurePopup();
  const send = () => {
    positionPopup(win);
    win.showInactive(); // show without activating -> no focus theft
    win.webContents.send('show-event', event);
  };
  if (win.webContents.isLoading()) {
    win.webContents.once('did-finish-load', send);
  } else {
    send();
  }
}

function dismissPopup() {
  if (popupWin && !popupWin.isDestroyed()) popupWin.hide();
  popupBusy = false;
  setTimeout(drainPopupQueue, 250);
}

// ----------------------------------------------------------------------
// Secondary windows
// ----------------------------------------------------------------------
function openSettings() {
  if (settingsWin && !settingsWin.isDestroyed()) {
    settingsWin.focus();
    return;
  }
  settingsWin = new BrowserWindow({
    width: 640,
    height: 480,
    title: 'Lumia 设置',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true },
  });
  settingsWin.loadFile(path.join(__dirname, 'settings.html'));
  settingsWin.on('closed', () => { settingsWin = null; });
}

function openAchievements() {
  if (achievementsWin && !achievementsWin.isDestroyed()) {
    achievementsWin.focus();
    return;
  }
  achievementsWin = new BrowserWindow({
    width: 560,
    height: 620,
    title: 'Lumia 今日成就墙',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      additionalArguments: ['--lumia-base=' + BASE],
    },
  });
  achievementsWin.loadFile(path.join(__dirname, 'achievements.html'));
  achievementsWin.on('closed', () => { achievementsWin = null; });
}

// ----------------------------------------------------------------------
// Event polling
// ----------------------------------------------------------------------
async function initLastEventId() {
  try {
    const state = await api('/api/state');
    lastEventId = state.latest_event_id || 0;
  } catch (_) { /* backend not up yet; will retry on next poll */ }
}

async function pollEvents() {
  try {
    const data = await api('/api/events/poll?after=' + lastEventId);
    for (const event of data.events || []) {
      lastEventId = Math.max(lastEventId, event.id);
      enqueuePopup(event);
    }
  } catch (_) { /* backend offline; ignore */ }
}

// ----------------------------------------------------------------------
// IPC from popup renderer
// ----------------------------------------------------------------------
ipcMain.on('popup-action', async (_evt, payload) => {
  const id = payload && payload.id;
  if (id === 'delay') {
    try { await api('/api/coding/delay', { method: 'POST' }); } catch (_) {}
  } else if (id === 'achievements') {
    openAchievements();
  }
  dismissPopup();
});

ipcMain.on('popup-dismiss', dismissPopup);

// ----------------------------------------------------------------------
// Tray + lifecycle
// ----------------------------------------------------------------------
function buildTray() {
  tray = new Tray(makeTrayIcon());
  tray.setToolTip('Lumia — 通宵者的睡前仪式');
  const menu = Menu.buildFromTemplate([
    { label: '查看今日状态', click: showStatusPopup },
    { label: '成就墙', click: openAchievements },
    { label: '请求延时', click: async () => { try { await api('/api/coding/delay', { method: 'POST' }); } catch (_) {} } },
    { type: 'separator' },
    { label: '设置…', click: openSettings },
    { type: 'separator' },
    { label: '退出 Lumia', click: () => { app.quit(); } },
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
      type: 'status',
      title: 'Lumia 今日状态',
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
  initLastEventId().then(() => {
    setInterval(pollEvents, CFG.pollIntervalMs);
  });
});

// Keep running in the tray even when all windows are closed.
app.on('window-all-closed', (e) => { /* no-op: tray app */ });

app.on('before-quit', () => {
  if (backendProc && !backendProc.killed) {
    try { backendProc.kill(); } catch (_) {}
  }
});
