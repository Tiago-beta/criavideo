const { contextBridge, shell } = require("electron");

contextBridge.exposeInMainWorld("CRIAVIDEO_DESKTOP_SHELL", {
    isDesktop: true,
    platform: process.platform,
    openExternal(url) {
        return shell.openExternal(String(url || ""));
    },
});
