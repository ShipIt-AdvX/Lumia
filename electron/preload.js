'use strict';
const { contextBridge, ipcRenderer } = require('electron');

function readArg(prefix) {
  const arg = process.argv.find((a) => a.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : '';
}

const baseUrl = readArg('--lumia-base=') || 'http://127.0.0.1:8787';
const title = readArg('--lumia-title=') || 'Lumia';

contextBridge.exposeInMainWorld('lumia', {
  baseUrl,
  title,

  onEvent: (cb) => ipcRenderer.on('show-event', (_e, data) => cb(data)),
  action: (payload) => ipcRenderer.send('popup-action', payload),
  dismiss: () => ipcRenderer.send('popup-dismiss'),

  win: {
    close: () => ipcRenderer.send('win-close'),
    toggleFullscreen: () => ipcRenderer.send('win-toggle-fullscreen'),
    collapseToggle: () => ipcRenderer.send('float-collapse-toggle'),
    dragStart: () => ipcRenderer.send('float-drag-start'),
    dragEnd: () => ipcRenderer.send('float-drag-end'),
    setHover: (hovering) => ipcRenderer.send('float-hover', hovering),
    reportHeight: (h) => ipcRenderer.send('float-content-height', h),
    onRetractedChanged: (cb) =>
      ipcRenderer.on('retracted-changed', (_e, retracted) => cb(retracted)),
    onFullscreenChanged: (cb) =>
      ipcRenderer.on('fullscreen-changed', (_e, isFull) => cb(isFull)),
    onFsZoom: (cb) =>
      ipcRenderer.on('fs-zoom', (_e, zoom) => cb(zoom)),
  },

  pickDirectory: () => ipcRenderer.invoke('pick-directory'),

  lockdown: {
    requestDelay: () => ipcRenderer.invoke('lockdown-delay'),
    requestSave: () => ipcRenderer.invoke('lockdown-save'),
    cancel: () => ipcRenderer.send('lockdown-cancel'),
  },

  dev: {
    emit: (event) => ipcRenderer.send('dev-emit', event),
    open: (key) => ipcRenderer.send('dev-open', key),
  },

  fetchJSON: async (pathname) => {
    const res = await fetch(baseUrl + pathname);
    return res.json();
  },
  putJSON: async (pathname, body) => {
    const res = await fetch(baseUrl + pathname, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    return res.json();
  },
  postJSON: async (pathname, body) => {
    const res = await fetch(baseUrl + pathname, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    });
    return res.json();
  },
});
