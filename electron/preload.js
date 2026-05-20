const { contextBridge, ipcRenderer } = require('electron');

// Expose minimal API to renderer (HUD page)
contextBridge.exposeInMainWorld('electronJarvis', {
  toggleGhost:   () => ipcRenderer.send('toggle-ghost'),
  toggleVisible: () => ipcRenderer.send('toggle-visible'),
  isElectron:    true,
});
