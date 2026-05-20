const {
  app, BrowserWindow, ipcMain,
  Tray, Menu, globalShortcut,
  nativeImage, screen, shell,
} = require('electron');
const path = require('path');

const HUD_URL = 'https://jarvis-yejun.duckdns.org/hud';

let win        = null;
let tray       = null;
let isVisible  = true;
let isOnTop    = true;
let isGhost    = false;   // click-through mode

// ── Tray icon (16×16 cyan circle, generated in-memory) ───────────────────────
function makeTrayIcon() {
  const size = 16;
  const buf  = Buffer.alloc(size * size * 4, 0);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const dist = Math.sqrt((x - 7.5) ** 2 + (y - 7.5) ** 2);
      const i    = (y * size + x) * 4;
      if (dist < 6.5) {
        buf[i]     = 0;    // R
        buf[i + 1] = 212;  // G
        buf[i + 2] = 255;  // B
        buf[i + 3] = dist < 5.5 ? 220 : 120;  // A (soft edge)
      }
    }
  }
  return nativeImage.createFromBuffer(buf, { width: size, height: size });
}

// ── BrowserWindow ─────────────────────────────────────────────────────────────
function createWindow() {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize;

  win = new BrowserWindow({
    width,
    height,
    x: 0,
    y: 0,
    frame:       false,
    transparent: true,
    backgroundColor: '#00000000',
    alwaysOnTop: true,
    skipTaskbar: false,
    resizable:   true,
    movable:     true,
    hasShadow:   false,
    webPreferences: {
      nodeIntegration:  false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  win.setAlwaysOnTop(true, 'screen-saver');
  win.loadURL(HUD_URL);

  win.webContents.on('did-finish-load', injectOverlayCSS);

  // Re-inject after in-page navigation
  win.webContents.on('did-navigate-in-page', injectOverlayCSS);
}

// ── CSS injected into the HUD page ───────────────────────────────────────────
function injectOverlayCSS() {
  if (!win) return;
  win.webContents.insertCSS(`
    /* Transparent body so Electron window transparency shows */
    html, body {
      background: rgba(1, 8, 16, 0.88) !important;
    }
    /* Top bar is draggable */
    #topbar {
      -webkit-app-region: drag;
      cursor: move !important;
    }
    /* All interactive elements inside topbar must opt out */
    #topbar button,
    #topbar .nav-btn,
    #topbar .t-nav,
    #topbar #ws-chip,
    #topbar #clock,
    #topbar a {
      -webkit-app-region: no-drag;
    }
    /* Everything else stays non-draggable */
    #left, #center, #right, #bot {
      -webkit-app-region: no-drag;
    }
    /* Scrollbars */
    ::-webkit-scrollbar       { width: 4px; height: 4px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(0,212,255,0.25); border-radius: 2px; }
  `);
}

// ── Tray ──────────────────────────────────────────────────────────────────────
function buildTrayMenu() {
  return Menu.buildFromTemplate([
    { label: 'J.A.R.V.I.S  HUD', enabled: false },
    { type: 'separator' },
    {
      label: isVisible ? 'HUD 숨기기  (Ctrl+Shift+J)' : 'HUD 보이기  (Ctrl+Shift+J)',
      click: toggleVisible,
    },
    {
      label: isOnTop ? '항상 위 OFF' : '항상 위 ON',
      click: toggleOnTop,
    },
    {
      label: isGhost ? '🌐 Ghost 모드 OFF  (Ctrl+Shift+G)' : '👻 Ghost 모드  (Ctrl+Shift+G)',
      click: toggleGhost,
    },
    { type: 'separator' },
    { label: '🔄 새로고침', click: () => win?.webContents.reload() },
    { label: '🌐 브라우저로 열기', click: () => shell.openExternal(HUD_URL) },
    { type: 'separator' },
    { label: '종료', click: () => app.exit(0) },
  ]);
}

function createTray() {
  tray = new Tray(makeTrayIcon());
  tray.setToolTip('J.A.R.V.I.S HUD');
  tray.setContextMenu(buildTrayMenu());
  tray.on('click', toggleVisible);
}

function refreshTray() {
  tray?.setContextMenu(buildTrayMenu());
}

// ── Actions ───────────────────────────────────────────────────────────────────
function toggleVisible() {
  if (!win) return;
  isVisible = !isVisible;
  isVisible ? win.show() : win.hide();
  refreshTray();
}

function toggleOnTop() {
  if (!win) return;
  isOnTop = !isOnTop;
  win.setAlwaysOnTop(isOnTop, 'screen-saver');
  refreshTray();
}

function toggleGhost() {
  if (!win) return;
  isGhost = !isGhost;
  // forward: true passes mouse events to windows beneath
  win.setIgnoreMouseEvents(isGhost, { forward: true });
  // Visual feedback via tray tooltip
  tray?.setToolTip(isGhost ? 'J.A.R.V.I.S  [GHOST]' : 'J.A.R.V.I.S HUD');
  refreshTray();
}

// ── IPC (from renderer via preload) ──────────────────────────────────────────
ipcMain.on('toggle-ghost', toggleGhost);
ipcMain.on('toggle-visible', toggleVisible);

// ── App lifecycle ─────────────────────────────────────────────────────────────
app.whenReady().then(() => {
  // Allow loading from the Oracle server
  app.commandLine.appendSwitch('ignore-certificate-errors');

  createWindow();
  createTray();

  // Global shortcuts
  globalShortcut.register('CommandOrControl+Shift+J', toggleVisible);
  globalShortcut.register('CommandOrControl+Shift+G', toggleGhost);

  // Auto-start with Windows
  app.setLoginItemSettings({ openAtLogin: true, name: 'JARVIS HUD' });
});

// Keep running in tray when all windows close
app.on('window-all-closed', (e) => e.preventDefault());

app.on('will-quit', () => globalShortcut.unregisterAll());
