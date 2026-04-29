if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
      navigator.serviceWorker.register("/video/static/sw.js?v=20260428-101").catch(() => {
      // Ignore SW registration failures to avoid blocking the app.
    });
  });
}
