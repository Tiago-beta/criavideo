if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/video/static/sw.js").catch(() => {
      // Ignore SW registration failures to avoid blocking the app.
    });
  });
}
