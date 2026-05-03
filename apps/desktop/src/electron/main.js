/**
 * Core Helper - Electron Main Process
 * Robust startup with crash-safe IPC and global hotkeys
 */

const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');

// Stealth v2 does not use a .env file at runtime. User config (server URL,
// license, provider, key, language, interview context) lives in
// ~/.stealth/config.json, managed by the Python backend.

// =============================================================================
// STEP 2: Import Electron with robust error handling
// =============================================================================
let app, BrowserWindow, ipcMain, screen, globalShortcut, desktopCapturer, dialog;

try {
    const electron = require('electron');
    app = electron.app;
    BrowserWindow = electron.BrowserWindow;
    ipcMain = electron.ipcMain;
    screen = electron.screen;
    globalShortcut = electron.globalShortcut;
    desktopCapturer = electron.desktopCapturer;
    dialog = electron.dialog;
} catch (err) {
    console.error('═══════════════════════════════════════════════════════════════');
    console.error('❌ CRITICAL ERROR: Failed to import Electron');
    console.error('═══════════════════════════════════════════════════════════════');
    console.error('Error:', err.message);
    console.error('');
    console.error('Correct usage: npm run start-electron');
    console.error('═══════════════════════════════════════════════════════════════');
    process.exit(1);
}

// =============================================================================
// STEP 3: Validate Electron app object
// =============================================================================
if (!app || typeof app.disableHardwareAcceleration !== 'function') {
    console.error('❌ CRITICAL ERROR: Electron "app" object is undefined or invalid');
    console.error('You are likely running this script with "node" instead of "electron".');
    console.error('✅ CORRECT: npm run start-electron');
    process.exit(1);
}

// =============================================================================
// STEP 4: Configure Electron settings
// =============================================================================
app.disableHardwareAcceleration();
console.log('✅ Hardware acceleration disabled');

if (process.platform === 'darwin') {
    app.dock.hide();
    console.log('✅ Dock icon hidden (macOS)');
}

let mainWindow = null;
let pythonProcess = null;
let isGhostMode = false;  // Track ghost mode state globally

// =============================================================================
// SAFE IPC SEND - Prevents "Render frame was disposed" crashes
// =============================================================================
function safeSend(channel, ...args) {
    try {
        if (mainWindow &&
            !mainWindow.isDestroyed() &&
            mainWindow.webContents &&
            !mainWindow.webContents.isDestroyed() &&
            !mainWindow.webContents.isLoading()) {
            mainWindow.webContents.send(channel, ...args);
            return true;
        }
    } catch (err) {
        console.warn(`[safeSend] Failed to send ${channel}:`, err.message);
    }
    return false;
}

// =============================================================================
// ENVIRONMENT DETECTION
// =============================================================================
const isDev = !app.isPackaged;
console.log(`✅ Running in ${isDev ? 'DEVELOPMENT' : 'PRODUCTION'} mode`);

// =============================================================================
// PYTHON BACKEND MANAGEMENT
// =============================================================================
function getPythonBackendPath() {
    if (isDev) {
        return {
            mode: 'script',
            pythonPath: path.join(__dirname, '../../venv/bin/python3'),
            scriptPath: path.join(__dirname, '../python/backend.py'),
            cwd: path.join(__dirname, '../../')
        };
    } else {
        return {
            mode: 'binary',
            binaryPath: path.join(process.resourcesPath, 'bin', 'stealth-backend', 'stealth-backend'),
            envPath: path.join(process.resourcesPath, '.env'),
            cwd: path.join(process.resourcesPath, 'bin', 'stealth-backend')
        };
    }
}

function startPythonBackend() {
    if (pythonProcess) {
        console.log('Python backend already running');
        return true;
    }

    const backendConfig = getPythonBackendPath();
    console.log('Backend config:', JSON.stringify(backendConfig, null, 2));

    try {
        if (backendConfig.mode === 'script') {
            const { pythonPath, scriptPath, cwd } = backendConfig;

            if (!fs.existsSync(pythonPath)) {
                console.error('❌ Python venv not found at:', pythonPath);
                safeSend('backend-error', {
                    message: 'Python environment not found. Run ./start.sh first.'
                });
                return false;
            }

            console.log('🚀 Starting Python backend (DEV mode)...');
            pythonProcess = spawn(pythonPath, [scriptPath], {
                cwd: cwd,
                env: { ...process.env, PYTHONUNBUFFERED: '1' }
            });

        } else {
            const { binaryPath, envPath, cwd } = backendConfig;

            if (!fs.existsSync(binaryPath)) {
                console.error('❌ Compiled backend not found at:', binaryPath);
                safeSend('backend-error', {
                    message: 'Backend binary not found. Please reinstall the app.'
                });
                return false;
            }

            try { fs.chmodSync(binaryPath, '755'); } catch (e) { }

            console.log('🚀 Starting Python backend (PROD mode)...');
            const prodEnv = { ...process.env, PYTHONUNBUFFERED: '1' };

            if (fs.existsSync(envPath)) {
                const envContent = fs.readFileSync(envPath, 'utf8');
                envContent.split('\n').forEach(line => {
                    const match = line.match(/^([^=]+)=(.*)$/);
                    if (match && !match[1].startsWith('#')) {
                        prodEnv[match[1].trim()] = match[2].trim();
                    }
                });
            }

            pythonProcess = spawn(binaryPath, [], { cwd, env: prodEnv });
        }

        // Event handlers with safeSend
        pythonProcess.stdout.on('data', (data) => {
            const output = data.toString();
            console.log(`[Python] ${output}`);
            safeSend('python-log', output);

            if (output.includes('PY_BACKEND_READY')) {
                console.log('✨ Backend is ready for connection!');
                safeSend('backend-status', { running: true });
            }
        });

        pythonProcess.stderr.on('data', (data) => {
            const error = data.toString();
            console.error(`[Python Error] ${error}`);
            safeSend('python-log', `❌ ${error}`);
        });

        pythonProcess.on('error', (error) => {
            console.error('❌ Failed to start Python:', error);
            pythonProcess = null;
            safeSend('backend-error', { message: `Failed to start backend: ${error.message}` });
        });

        pythonProcess.on('close', (code) => {
            console.log(`Python process exited with code ${code}`);
            pythonProcess = null;
            safeSend('backend-status', { running: false });
        });

        console.log('✅ Python backend process spawned');
        safeSend('python-log', `🚀 Backend spawned (PID: ${pythonProcess.pid})`);
        return true;

    } catch (error) {
        console.error('❌ Failed to start Python backend:', error);
        safeSend('python-log', `❌ Spawn Error: ${error.message}`);
        return false;
    }
}

function stopPythonBackend() {
    if (pythonProcess) {
        console.log('Stopping Python backend...');
        pythonProcess.kill('SIGTERM');
        pythonProcess = null;
        return true;
    }
    return false;
}

// =============================================================================
// TOGGLE GHOST MODE - Used by both button click and global hotkey
// =============================================================================
function toggleGhostMode() {
    if (!mainWindow || mainWindow.isDestroyed()) return;

    isGhostMode = !isGhostMode;

    if (isGhostMode) {
        // Ghost Mode ON: Full click-through, no forwarding (use hotkey to exit)
        mainWindow.setIgnoreMouseEvents(true);
        console.log('👻 Ghost Mode: ON - Press ⌘⇧L to exit');
    } else {
        // Ghost Mode OFF: Fully interactive
        mainWindow.setIgnoreMouseEvents(false);
        console.log('🖐️ Ghost Mode: OFF - Window is interactive');
    }

    // Notify renderer to update the icon
    safeSend('ghost-mode-changed', isGhostMode);
}

// Force exit ghost mode (used for safety)
function forceExitGhostMode() {
    if (!mainWindow || mainWindow.isDestroyed()) return;

    isGhostMode = false;
    mainWindow.setIgnoreMouseEvents(false);
    safeSend('ghost-mode-changed', false);
    console.log('🖐️ Force exited Ghost Mode');
}

// =============================================================================
// CREATE WINDOW
// =============================================================================
function createWindow() {
    const { width, height } = screen.getPrimaryDisplay().workAreaSize;

    const SIDEBAR_WIDTH = 420;
    const SIDEBAR_HEIGHT = Math.min(800, height - 100);

    mainWindow = new BrowserWindow({
        width: SIDEBAR_WIDTH,
        height: SIDEBAR_HEIGHT,
        transparent: true,
        frame: false,
        alwaysOnTop: true,
        hasShadow: true,
        resizable: true,
        minWidth: 350,
        minHeight: 400,
        maxWidth: 600,
        skipTaskbar: true,
        focusable: true,
        webPreferences: {
            nodeIntegration: true,
            contextIsolation: false,
            backgroundThrottling: false
        }
    });

    // CRITICAL: Prevent screen capture
    mainWindow.setContentProtection(true);
    console.log('✅ Content protection enabled');

    // Start in interactive mode
    mainWindow.setIgnoreMouseEvents(false);
    isGhostMode = false;
    console.log('✅ Window starts in Interactive Mode');

    mainWindow.loadFile(path.join(__dirname, 'overlay.html'));

    mainWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
    mainWindow.setAlwaysOnTop(true, 'screen-saver');

    // =========================================================================
    // IPC HANDLERS (with safeSend protection)
    // =========================================================================

    // Ghost mode toggle from renderer
    ipcMain.on('set-ghost-mode', (event, enabled) => {
        const win = BrowserWindow.fromWebContents(event.sender);
        if (win && !win.isDestroyed()) {
            isGhostMode = enabled;
            if (enabled) {
                // Full click-through - use ⌘⇧L to exit
                win.setIgnoreMouseEvents(true);
                console.log('👻 Ghost Mode: ON - Press ⌘⇧L to exit');
            } else {
                win.setIgnoreMouseEvents(false);
                win.setIgnoreMouseEvents(false);
                console.log('🖐️ Ghost Mode: OFF');
            }
        }
    });

    // Smart Capture: Get Target Window Thumbnail (Stealthier than Python screenshot)
    ipcMain.handle('get-target-source', async () => {
        try {
            // First try to get specific windows
            const windowSources = await desktopCapturer.getSources({
                types: ['window'],
                thumbnailSize: { width: 1920, height: 1080 },
                fetchWindowIcons: false
            });

            console.log(`📋 Found ${windowSources.length} windows:`);
            windowSources.forEach(s => console.log(`   - ${s.name}`));

            // TARGET PRIORITY: Coding Apps > Browsers
            const TARGETS = [
                'two sum', 'leetcode', 'hackerrank', 'codechef', // Problem titles
                'code', 'visual studio', 'pycharm', 'intellij',
                'sublime', 'atom', 'vim', 'terminal', 'xcode',
                'chrome', 'safari', 'firefox', 'brave', 'arc', 'edge'
            ];

            // Filter out our own overlay and system windows
            const validSources = windowSources.filter(s => {
                const name = s.name.toLowerCase();
                return !name.includes('stealth') &&
                    !name.includes('systemsettings') &&
                    !name.includes('system settings') &&
                    !name.includes('overlay') &&
                    name.length > 0;
            });

            let bestSource = null;

            // 1. Look for exact keyword matches in title
            for (const keyword of TARGETS) {
                bestSource = validSources.find(s => s.name.toLowerCase().includes(keyword));
                if (bestSource) {
                    console.log(`🎯 Found target by keyword "${keyword}": ${bestSource.name}`);
                    break;
                }
            }

            // 2. Fallback: Any window that isn't system
            if (!bestSource && validSources.length > 0) {
                bestSource = validSources[0];
                console.log(`⚠️ No keyword match, using first valid: ${bestSource.name}`);
            }

            // 3. Last resort: Capture the ENTIRE SCREEN
            if (!bestSource) {
                console.log('📺 No window found, capturing entire screen...');
                const screenSources = await desktopCapturer.getSources({
                    types: ['screen'],
                    thumbnailSize: { width: 1920, height: 1080 }
                });
                if (screenSources.length > 0) {
                    bestSource = screenSources[0];
                    console.log(`📺 Captured screen: ${bestSource.name}`);
                }
            }

            if (bestSource) {
                console.log(`📸 Final capture: ${bestSource.name}`);
                return bestSource.thumbnail.toDataURL();
            }

            console.log('❌ No capture source available');
            return null;

        } catch (error) {
            console.error('Capture failed:', error);
            return null;
        }
    });

    // Legacy handler
    ipcMain.on('set-ignore-mouse-events', (event, ignore, options) => {
        const win = BrowserWindow.fromWebContents(event.sender);
        if (win && !win.isDestroyed()) {
            win.setIgnoreMouseEvents(ignore, options || {});
        }
    });

    // Backend controls
    ipcMain.on('start-backend', () => {
        const success = startPythonBackend();
        safeSend('backend-status', { running: success });
    });

    ipcMain.on('stop-backend', () => {
        stopPythonBackend();
        safeSend('backend-status', { running: false });
    });

    ipcMain.on('quit-app', () => {
        console.log('🛑 Quitting app...');
        stopPythonBackend();
        
        // Short delay to allow backend to clean up, then force exit
        setTimeout(() => {
            app.quit();
            // Force exit after another short delay if app.quit() is hindered
            setTimeout(() => {
                console.log('💥 Force exiting process');
                process.exit(0);
            }, 500);
        }, 300);
    });

    // ---- Setup flow IPC ----
    // Renderer → main.js: open a file dialog to pick a resume.
    // Returns the absolute path or null if cancelled. The renderer then
    // hands the path to the Python backend via socket.io ('save_setup'),
    // which parses + saves it. Keeps main.js thin.
    ipcMain.handle('pick-resume-file', async () => {
        try {
            const result = await dialog.showOpenDialog(mainWindow, {
                title: 'Select your resume',
                properties: ['openFile'],
                filters: [
                    { name: 'Resume', extensions: ['pdf', 'docx', 'txt', 'md'] },
                ],
            });
            if (result.canceled || result.filePaths.length === 0) return null;
            return result.filePaths[0];
        } catch (err) {
            console.error('pick-resume-file failed:', err);
            return null;
        }
    });

    // Read ~/.stealth/config.json from the renderer (without exposing the key).
    // Used to decide whether to show the first-run setup modal.
    ipcMain.handle('get-config-status', async () => {
        try {
            const home = require('os').homedir();
            const cfgPath = path.join(home, '.stealth', 'config.json');
            if (!fs.existsSync(cfgPath)) {
                return { configured: false };
            }
            const raw = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
            return {
                configured: Boolean(raw.provider && raw.api_key),
                provider: raw.provider || '',
                model: raw.model || '',
            };
        } catch (err) {
            console.error('get-config-status failed:', err);
            return { configured: false };
        }
    });

    // Auto-start backend after window is fully loaded
    mainWindow.webContents.on('did-finish-load', () => {
        console.log('✅ Window finished loading');
        // We no longer send status here; we wait for 'PY_BACKEND_READY' from stdout
        setTimeout(() => {
            startPythonBackend();
        }, 500);
    });

    // Handle window close
    mainWindow.on('closed', () => {
        mainWindow = null;
    });
}

// =============================================================================
// REGISTER GLOBAL HOTKEYS
// =============================================================================
function registerGlobalShortcuts() {
    // PANIC BUTTON: Cmd+Shift+L (Mac) / Ctrl+Shift+L (Win)
    const accelerator = process.platform === 'darwin' ? 'Command+Shift+L' : 'Ctrl+Shift+L';

    const registered = globalShortcut.register(accelerator, () => {
        console.log('🔥 Panic button pressed!');
        toggleGhostMode();
    });

    // DEBUG CONSOLE: Cmd+Shift+T (Mac) / Ctrl+Shift+T (Win)
    const debugAccelerator = process.platform === 'darwin' ? 'Command+Shift+T' : 'Ctrl+Shift+T';
    globalShortcut.register(debugAccelerator, () => {
        console.log('🛠️ Debug hotkey pressed!');
        safeSend('toggle-debug-logs');
    });

    // CAPTURE VISION: Cmd+Option+C
    const captureAccelerator = process.platform === 'darwin' ? 'Command+Option+C' : 'Ctrl+Alt+C';
    globalShortcut.register(captureAccelerator, () => {
        console.log('📸 Capture hotkey pressed!');
        safeSend('trigger-vision-capture');
    });

    if (registered) {
        console.log(`✅ Global hotkey registered: ${accelerator}`);
        console.log(`✅ Debug hotkey registered: ${debugAccelerator}`);
        console.log(`✅ Capture hotkey registered: ${captureAccelerator}`);
    } else {
        console.error(`❌ Failed to register global hotkey: ${accelerator}`);
    }
}

// =============================================================================
// APP LIFECYCLE
// =============================================================================
app.whenReady().then(() => {
    console.log('✅ Electron app ready');
    createWindow();
    registerGlobalShortcuts();

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createWindow();
        }
    });
});

app.on('window-all-closed', () => {
    stopPythonBackend();
    if (process.platform !== 'darwin') {
        app.quit();
    }
});

app.on('will-quit', () => {
    // Unregister all shortcuts
    globalShortcut.unregisterAll();
    console.log('✅ Global shortcuts unregistered');
});

app.on('before-quit', () => {
    stopPythonBackend();
});

// Handle uncaught exceptions
process.on('uncaughtException', (error) => {
    console.error('Uncaught exception:', error);
    stopPythonBackend();
});
