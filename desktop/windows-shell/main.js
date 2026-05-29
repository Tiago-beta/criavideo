const fs = require("fs");
const path = require("path");
const { app, BrowserWindow, shell } = require("electron");

const DEFAULT_TARGET_URL = "https://criavideo.pro/video";
const DEFAULT_WINDOW = {
    width: 1480,
    height: 960,
    minWidth: 1220,
    minHeight: 760,
};
const CONFIG_FILE_NAME = "desktop-config.json";

function resolveConfigPath() {
    const packagedPath = path.join(process.resourcesPath, CONFIG_FILE_NAME);
    if (app.isPackaged && fs.existsSync(packagedPath)) {
        return packagedPath;
    }
    return path.join(__dirname, CONFIG_FILE_NAME);
}

function loadDesktopConfig() {
    const configPath = resolveConfigPath();
    try {
        const raw = fs.readFileSync(configPath, "utf8");
        const parsed = JSON.parse(raw);
        const targetUrl = String(parsed?.targetUrl || DEFAULT_TARGET_URL).trim() || DEFAULT_TARGET_URL;
        return {
            targetUrl,
            window: {
                ...DEFAULT_WINDOW,
                ...(parsed?.window || {}),
            },
        };
    } catch (error) {
        return {
            targetUrl: DEFAULT_TARGET_URL,
            window: { ...DEFAULT_WINDOW },
        };
    }
}

function isInternalDesktopUrl(candidateUrl, desktopConfig) {
    try {
        const target = new URL(desktopConfig.targetUrl);
        const next = new URL(candidateUrl);
        return next.origin === target.origin && next.pathname.startsWith("/video");
    } catch (error) {
        return false;
    }
}

function createMainWindow() {
    const desktopConfig = loadDesktopConfig();
    const mainWindow = new BrowserWindow({
        width: Number(desktopConfig.window.width || DEFAULT_WINDOW.width),
        height: Number(desktopConfig.window.height || DEFAULT_WINDOW.height),
        minWidth: Number(desktopConfig.window.minWidth || DEFAULT_WINDOW.minWidth),
        minHeight: Number(desktopConfig.window.minHeight || DEFAULT_WINDOW.minHeight),
        backgroundColor: "#081f35",
        autoHideMenuBar: true,
        title: "CriaVideo Desktop",
        webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: false,
            preload: path.join(__dirname, "preload.js"),
        },
    });

    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        if (isInternalDesktopUrl(url, desktopConfig)) {
            return { action: "allow" };
        }
        shell.openExternal(url);
        return { action: "deny" };
    });

    mainWindow.webContents.on("will-navigate", (event, url) => {
        if (isInternalDesktopUrl(url, desktopConfig)) {
            return;
        }
        event.preventDefault();
        shell.openExternal(url);
    });

    mainWindow.loadURL(desktopConfig.targetUrl);
    return mainWindow;
}

app.whenReady().then(() => {
    createMainWindow();

    app.on("activate", () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            createMainWindow();
        }
    });
});

app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
        app.quit();
    }
});
