if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/video/static/sw.js?v=20260412-13").catch(() => {
      // Ignore SW registration failures to avoid blocking the app.
    });
  });
}
