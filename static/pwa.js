if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/video/static/sw.js?v=20260403-8").catch(() => {
      // Ignore SW registration failures to avoid blocking the app.
    });
  });
}
