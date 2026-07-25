'use strict';
const { contextBridge, ipcRenderer } = require('electron');

function readArg(prefix) {
  const arg = process.argv.find((a) => a.startsWith(prefix));
  return arg ? arg.slice(prefix.length) : '';
}

const baseUrl = readArg('--lumia-base=') || 'http://127.0.0.1:8787';
const title = readArg('--lumia-title=') || 'Lumia';
const isPet = process.argv.includes('--lumia-pet');

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
    onRetractedChanged: (cb) =>
      ipcRenderer.on('retracted-changed', (_e, retracted) => cb(retracted)),
    onFullscreenChanged: (cb) =>
      ipcRenderer.on('fullscreen-changed', (_e, isFull) => cb(isFull)),
  },

  pickDirectory: () => ipcRenderer.invoke('pick-directory'),

  dev: {
    emit: (event) => ipcRenderer.send('dev-emit', event),
    open: (key) => ipcRenderer.send('dev-open', key),
    petCmd: (action) => ipcRenderer.send('dev-pet-cmd', action),
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
if (isPet) {
  contextBridge.exposeInMainWorld('electronAPI', {
    movePet: (x, y) => ipcRenderer.send('pet-move', x, y),
    petDragStart: () => ipcRenderer.send('pet-drag-start'),
    petDragEnd: () => ipcRenderer.send('pet-drag-end'),
    togglePet: () => ipcRenderer.send('pet-toggle'),
    onPetCmd: (cb) => ipcRenderer.on('pet-cmd', (_e, action) => cb(action)),
  });
}
