const { contextBridge, shell } = require("electron");

function readDesktopArg(prefix, fallback = "") {
    const match = process.argv.find((item) => String(item || "").startsWith(prefix));
    if (!match) return fallback;
    return String(match.slice(prefix.length) || fallback);
}

const runtimeMode = readDesktopArg("--criavideo-runtime-mode=", "remote");
const targetUrl = decodeURIComponent(readDesktopArg("--criavideo-target-url=", ""));

contextBridge.exposeInMainWorld("CRIAVIDEO_DESKTOP_SHELL", {
    isDesktop: true,
    platform: process.platform,
    runtimeMode,
    targetUrl,
    isLocalRuntime: runtimeMode === "local-proxy",
    openExternal(url) {
        return shell.openExternal(String(url || ""));
    },
});
