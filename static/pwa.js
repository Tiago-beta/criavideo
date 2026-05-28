if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/video/static/sw.js?v=20260528-09").catch(() => {
      // Ignore SW registration failures to avoid blocking the app.
    });
  });
}
