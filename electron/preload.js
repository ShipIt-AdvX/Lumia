'use strict';
/** Preload bridge shared by popup / settings / achievements windows. */
const { contextBridge, ipcRenderer } = require('electron');

function readBaseUrl() {
  const arg = process.argv.find((a) => a.startsWith('--lumia-base='));
  return arg ? arg.slice('--lumia-base='.length) : 'http://127.0.0.1:8787';
}

const baseUrl = readBaseUrl();

contextBridge.exposeInMainWorld('lumia', {
  baseUrl,
  // popup -> main
  onEvent: (cb) => ipcRenderer.on('show-event', (_e, data) => cb(data)),
  action: (payload) => ipcRenderer.send('popup-action', payload),
  dismiss: () => ipcRenderer.send('popup-dismiss'),
  // direct HTTP for read-only windows (achievements)
  fetchJSON: async (pathname) => {
    const res = await fetch(baseUrl + pathname);
    return res.json();
  },
});
