(() => {
  "use strict";

  const preferenceKey = "oss-singularity-theme";
  const root = document.documentElement;

  function applyTheme(value) {
    const theme = value === "bright" ? "bright" : "dark";
    const changed = root.dataset.theme !== theme;
    root.dataset.theme = theme;
    for (const meta of document.querySelectorAll('meta[name="theme-color"]')) {
      meta.setAttribute("content", theme === "bright" ? "#f4f7fb" : "#07111f");
    }
    for (const meta of document.querySelectorAll('meta[name="color-scheme"]')) {
      meta.setAttribute("content", theme === "bright" ? "light" : "dark");
    }
    syncActions();
    // Palette consumers read dataset.theme at startup, then listen on document.
    if (changed) document.dispatchEvent(new CustomEvent("oss-theme-change", { detail: { theme } }));
  }

  // The icon and the label swap through CSS: the markup carries both theme
  // pairs and html[data-theme] shows the matching one, so the control paints
  // correct from the very first frame. Only the accessible name needs scripting.
  function syncActions() {
    const action = root.dataset.theme === "bright" ? "Switch to dark mode" : "Switch to bright mode";
    for (const button of document.querySelectorAll("button[data-theme-toggle]")) {
      button.setAttribute("aria-label", action);
    }
  }

  let savedTheme = null;
  try {
    savedTheme = window.localStorage.getItem(preferenceKey);
  } catch {
    // A blocked preference store must not prevent the page from opening.
  }
  applyTheme(savedTheme);

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", syncActions, { once: true });
  } else {
    syncActions();
  }

  // One delegated listener covers every toggle from the moment this script
  // runs — there is no wiring window in which a visible button would ignore
  // a first click while the rest of the page is still loading.
  document.addEventListener("click", (event) => {
    const target = event && event.target;
    const button = target && typeof target.closest === "function" ? target.closest("button[data-theme-toggle]") : null;
    if (!button) return;
    applyTheme(root.dataset.theme === "bright" ? "dark" : "bright");
    try {
      window.localStorage.setItem(preferenceKey, root.dataset.theme);
    } catch {
      // The selected theme still works for this page when saving is blocked.
    }
  });

  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    try {
      // A suspended page may have missed preference changes in another tab.
      applyTheme(window.localStorage.getItem(preferenceKey));
    } catch {
      // Keep the restored page usable in its current theme if access is blocked.
    }
  });

  window.addEventListener("storage", (event) => {
    if (event.key !== preferenceKey && event.key !== null) return;
    try {
      if (event.storageArea !== window.localStorage) return;
    } catch {
      return;
    }
    // Removing or clearing the preference restores the dark first-visit default.
    applyTheme(event.newValue);
  });
})();
