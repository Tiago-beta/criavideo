const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");
const { app, BrowserWindow, shell } = require("electron");

const DEFAULT_TARGET_URL = "https://criavideo.pro/video";
const DEFAULT_RUNTIME = {
    mode: "remote",
    localUrl: "http://127.0.0.1:3232/video",
    healthUrl: "http://127.0.0.1:3232/video/health",
    apiTargetUrl: "",
    pythonCommand: "",
    entryScript: "local-runtime/app.py",
    staticDir: "static",
    startupTimeoutMs: 25000,
};
const DEFAULT_WINDOW = {
    width: 1480,
    height: 960,
    minWidth: 1220,
    minHeight: 760,
};
const CONFIG_FILE_NAME = "desktop-config.json";

let desktopRuntimeProcess = null;

function stripUtf8Bom(text) {
    return String(text || "").replace(/^\uFEFF/, "");
}

function readDesktopEnvOverride(name) {
    return String(process.env[name] || "").trim();
}

function resolveConfigPath() {
    const packagedPath = path.join(process.resourcesPath, CONFIG_FILE_NAME);
    if (app.isPackaged && fs.existsSync(packagedPath)) {
        return packagedPath;
    }
    return path.join(__dirname, CONFIG_FILE_NAME);
}

function normalizeOrigin(candidateUrl, fallbackUrl = DEFAULT_TARGET_URL) {
    try {
        return new URL(String(candidateUrl || fallbackUrl)).origin;
    } catch (error) {
        return new URL(fallbackUrl).origin;
    }
}

function resolveResourcePath(relativePath, devRootOffset = []) {
    const source = String(relativePath || "").trim();
    if (!source) {
        return "";
    }
    if (path.isAbsolute(source)) {
        return source;
    }
    if (app.isPackaged) {
        return path.join(process.resourcesPath, ...source.split(/[\\/]+/));
    }
    return path.join(__dirname, ...devRootOffset, ...source.split(/[\\/]+/));
}

function resolveRuntimeEntryScript(runtimeConfig) {
    return resolveResourcePath(runtimeConfig.entryScript || DEFAULT_RUNTIME.entryScript, [".."]);
}

function resolveRuntimeStaticDir(runtimeConfig) {
    return resolveResourcePath(runtimeConfig.staticDir || DEFAULT_RUNTIME.staticDir, ["..", ".."]);
}

function resolvePythonCommand(runtimeConfig) {
    const configured = String(runtimeConfig.pythonCommand || "").trim();
    if (configured) {
        return configured;
    }

    if (process.env.VIRTUAL_ENV) {
        const virtualEnvPython = path.join(
            process.env.VIRTUAL_ENV,
            process.platform === "win32" ? "Scripts" : "bin",
            process.platform === "win32" ? "python.exe" : "python3",
        );
        if (fs.existsSync(virtualEnvPython)) {
            return virtualEnvPython;
        }
    }

    if (!app.isPackaged) {
        const repoVenvPython = path.join(
            __dirname,
            "..",
            "..",
            ".venv",
            process.platform === "win32" ? "Scripts" : "bin",
            process.platform === "win32" ? "python.exe" : "python3",
        );
        if (fs.existsSync(repoVenvPython)) {
            return repoVenvPython;
        }
    }

    const packagedPython = path.join(
        process.resourcesPath,
        "python",
        process.platform === "win32" ? "python.exe" : path.join("bin", "python3"),
    );
    if (app.isPackaged && fs.existsSync(packagedPython)) {
        return packagedPython;
    }

    return process.platform === "win32" ? "python" : "python3";
}

async function waitForHealth(healthUrl, timeoutMs = DEFAULT_RUNTIME.startupTimeoutMs) {
    const deadline = Date.now() + Math.max(1000, Number(timeoutMs || DEFAULT_RUNTIME.startupTimeoutMs));
    let lastError = new Error(`Health check timed out: ${healthUrl}`);

    while (Date.now() < deadline) {
        try {
            const response = await fetch(healthUrl, { cache: "no-store" });
            if (response.ok) {
                return true;
            }
            lastError = new Error(`Health check returned ${response.status} for ${healthUrl}`);
        } catch (error) {
            lastError = error;
        }
        await new Promise((resolve) => setTimeout(resolve, 400));
    }

    throw lastError;
}

function stopDesktopRuntime() {
    if (!desktopRuntimeProcess || desktopRuntimeProcess.killed) {
        desktopRuntimeProcess = null;
        return;
    }

    const processToKill = desktopRuntimeProcess;
    desktopRuntimeProcess = null;

    if (process.platform === "win32") {
        spawn("taskkill", ["/pid", String(processToKill.pid), "/t", "/f"], { windowsHide: true });
        return;
    }

    processToKill.kill("SIGTERM");
}

async function ensureDesktopRuntime(desktopConfig) {
    const runtimeConfig = desktopConfig.runtime;
    if (runtimeConfig.mode !== "local-proxy") {
        return {
            startUrl: desktopConfig.targetUrl,
            runtimeMode: "remote",
            internalBaseUrl: desktopConfig.targetUrl,
        };
    }

    try {
        await waitForHealth(runtimeConfig.healthUrl, 1200);
        return {
            startUrl: runtimeConfig.localUrl,
            runtimeMode: runtimeConfig.mode,
            internalBaseUrl: runtimeConfig.localUrl,
        };
    } catch (error) {
        // Local runtime is not up yet; continue with bootstrap.
    }

    const pythonCommand = resolvePythonCommand(runtimeConfig);
    const entryScript = resolveRuntimeEntryScript(runtimeConfig);
    const staticDir = resolveRuntimeStaticDir(runtimeConfig);
    if (!fs.existsSync(entryScript)) {
        throw new Error(`Desktop runtime entry script not found: ${entryScript}`);
    }
    if (!fs.existsSync(staticDir)) {
        throw new Error(`Desktop runtime static directory not found: ${staticDir}`);
    }

    desktopRuntimeProcess = spawn(pythonCommand, [entryScript], {
        cwd: path.dirname(entryScript),
        windowsHide: true,
        env: {
            ...process.env,
            CRIAVIDEO_DESKTOP_RUNTIME_HOST: new URL(runtimeConfig.localUrl).hostname,
            CRIAVIDEO_DESKTOP_RUNTIME_PORT: String(new URL(runtimeConfig.localUrl).port || 3232),
            CRIAVIDEO_DESKTOP_RUNTIME_SITE_URL: runtimeConfig.apiTargetUrl,
            CRIAVIDEO_DESKTOP_RUNTIME_STATIC_DIR: staticDir,
        },
        stdio: ["ignore", "pipe", "pipe"],
    });

    desktopRuntimeProcess.stdout?.on("data", (chunk) => {
        console.log("[desktop-runtime]", String(chunk || "").trimEnd());
    });
    desktopRuntimeProcess.stderr?.on("data", (chunk) => {
        console.error("[desktop-runtime]", String(chunk || "").trimEnd());
    });
    const spawnedPid = desktopRuntimeProcess.pid;
    desktopRuntimeProcess.on("exit", (code, signal) => {
        console.log(`[desktop-runtime] exited with code=${code} signal=${signal || "none"}`);
        if (desktopRuntimeProcess && desktopRuntimeProcess.pid === spawnedPid) {
            desktopRuntimeProcess = null;
        }
    });

    await waitForHealth(runtimeConfig.healthUrl, runtimeConfig.startupTimeoutMs);
    return {
        startUrl: runtimeConfig.localUrl,
        runtimeMode: runtimeConfig.mode,
        internalBaseUrl: runtimeConfig.localUrl,
    };
}

function buildRuntimeErrorUrl(error, desktopConfig) {
    const message = String(error?.message || error || "Falha ao iniciar o runtime local.");
    const fallbackUrl = String(desktopConfig.targetUrl || DEFAULT_TARGET_URL).trim() || DEFAULT_TARGET_URL;
    return `data:text/html;charset=UTF-8,${encodeURIComponent(`<!doctype html><html><head><meta charset="utf-8"><title>CriaVideo Desktop</title><style>body{font-family:Segoe UI,Arial,sans-serif;background:#081f35;color:#f5fbff;padding:32px}main{max-width:680px;margin:0 auto}a{color:#ffca55}</style></head><body><main><h1>Falha ao iniciar o runtime local</h1><p>${message}</p><p>O host desktop conseguiu abrir o shell, mas o runtime local não respondeu ao health check.</p><p><a href="${fallbackUrl}">Abrir site remoto</a></p></main></body></html>`)}`;
}

function loadDesktopConfig() {
    const configPath = resolveConfigPath();
    try {
        const raw = stripUtf8Bom(fs.readFileSync(configPath, "utf8"));
        const parsed = JSON.parse(raw);
        const envTargetUrl = readDesktopEnvOverride("CRIAVIDEO_DESKTOP_TARGET_URL");
        let targetUrl = String(parsed?.targetUrl || DEFAULT_TARGET_URL).trim() || DEFAULT_TARGET_URL;
        if (envTargetUrl) {
            targetUrl = envTargetUrl;
        }
        const runtimeConfig = {
            ...DEFAULT_RUNTIME,
            ...(parsed?.runtime || {}),
        };
        const envRuntimeMode = readDesktopEnvOverride("CRIAVIDEO_DESKTOP_RUNTIME_MODE");
        const envLocalUrl = readDesktopEnvOverride("CRIAVIDEO_DESKTOP_LOCAL_URL");
        const envHealthUrl = readDesktopEnvOverride("CRIAVIDEO_DESKTOP_HEALTH_URL");
        const envApiTargetUrl = readDesktopEnvOverride("CRIAVIDEO_DESKTOP_API_TARGET_URL");
        runtimeConfig.mode = String(envRuntimeMode || runtimeConfig.mode || DEFAULT_RUNTIME.mode).trim().toLowerCase() === "local-proxy"
            ? "local-proxy"
            : "remote";
        runtimeConfig.localUrl = String(envLocalUrl || runtimeConfig.localUrl || DEFAULT_RUNTIME.localUrl).trim() || DEFAULT_RUNTIME.localUrl;
        runtimeConfig.healthUrl = String(envHealthUrl || runtimeConfig.healthUrl || DEFAULT_RUNTIME.healthUrl).trim() || DEFAULT_RUNTIME.healthUrl;
        runtimeConfig.apiTargetUrl = normalizeOrigin(envApiTargetUrl || runtimeConfig.apiTargetUrl || targetUrl, targetUrl);
        runtimeConfig.startupTimeoutMs = Math.max(5000, Number(runtimeConfig.startupTimeoutMs || DEFAULT_RUNTIME.startupTimeoutMs));
        return {
            targetUrl,
            runtime: runtimeConfig,
            window: {
                ...DEFAULT_WINDOW,
                ...(parsed?.window || {}),
            },
        };
    } catch (error) {
        console.warn(`[desktop-config] failed to load ${configPath}: ${error?.message || error}`);
        return {
            targetUrl: DEFAULT_TARGET_URL,
            runtime: { ...DEFAULT_RUNTIME, apiTargetUrl: normalizeOrigin(DEFAULT_TARGET_URL, DEFAULT_TARGET_URL) },
            window: { ...DEFAULT_WINDOW },
        };
    }
}

function isInternalDesktopUrl(candidateUrl, internalBaseUrl) {
    try {
        const target = new URL(internalBaseUrl);
        const next = new URL(candidateUrl);
        return next.origin === target.origin && next.pathname.startsWith("/video");
    } catch (error) {
        return false;
    }
}

async function createMainWindow() {
    const desktopConfig = loadDesktopConfig();
    let startUrl = desktopConfig.targetUrl;
    let runtimeMode = "remote";
    let internalBaseUrl = desktopConfig.targetUrl;

    try {
        const runtimeState = await ensureDesktopRuntime(desktopConfig);
        startUrl = runtimeState.startUrl;
        runtimeMode = runtimeState.runtimeMode;
        internalBaseUrl = runtimeState.internalBaseUrl;
    } catch (error) {
        console.error("[desktop-runtime] failed to bootstrap local runtime", error);
        startUrl = buildRuntimeErrorUrl(error, desktopConfig);
        runtimeMode = "runtime-error";
        internalBaseUrl = desktopConfig.targetUrl;
    }

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
            additionalArguments: [
                `--criavideo-runtime-mode=${runtimeMode}`,
                `--criavideo-target-url=${encodeURIComponent(startUrl)}`,
            ],
        },
    });

    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        if (isInternalDesktopUrl(url, internalBaseUrl)) {
            return { action: "allow" };
        }
        shell.openExternal(url);
        return { action: "deny" };
    });

    mainWindow.webContents.on("will-navigate", (event, url) => {
        if (isInternalDesktopUrl(url, internalBaseUrl)) {
            return;
        }
        event.preventDefault();
        shell.openExternal(url);
    });

    mainWindow.loadURL(startUrl);
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

app.on("before-quit", () => {
    stopDesktopRuntime();
});

app.on("window-all-closed", () => {
    if (process.platform !== "darwin") {
        stopDesktopRuntime();
        app.quit();
    }
});
