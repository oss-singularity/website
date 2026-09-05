(() => {
  "use strict";

  const preferenceKey = "oss-singularity-theme";
  const root = document.documentElement;
  let buttons = [];

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
    for (const button of buttons) {
      button.setAttribute("aria-label", theme === "bright" ? "Switch to dark mode" : "Switch to bright mode");
      const icon = button.querySelector("[data-theme-icon]");
      const label = button.querySelector("[data-theme-label]");
      if (icon) icon.textContent = theme === "bright" ? "☾" : "☀";
      if (label) label.textContent = theme === "bright" ? "Dark mode" : "Bright mode";
    }
    // Palette consumers read dataset.theme at startup, then listen on document.
    if (changed) document.dispatchEvent(new CustomEvent("oss-theme-change", { detail: { theme } }));
  }

  let savedTheme = null;
  try {
    savedTheme = window.localStorage.getItem(preferenceKey);
  } catch {
    // A blocked preference store must not prevent the page from opening.
  }
  applyTheme(savedTheme);

  function connectButtons() {
    buttons = Array.from(document.querySelectorAll("button[data-theme-toggle]"));
    for (const button of buttons) {
      button.addEventListener("click", () => {
        const theme = root.dataset.theme === "bright" ? "dark" : "bright";
        applyTheme(theme);
        try {
          window.localStorage.setItem(preferenceKey, theme);
        } catch {
          // The selected theme still works for this page when saving is blocked.
        }
      });
      button.hidden = false;
    }
    applyTheme(root.dataset.theme);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connectButtons, { once: true });
  } else {
    connectButtons();
  }

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
