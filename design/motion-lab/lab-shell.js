(() => {
  "use strict";
  const frame = document.querySelector("#preview");
  let variant = "thin-energy", theme = "dark";
  const reload = () => { frame.src = `preview.html?variant=${variant}&theme=${theme}`; };
  document.querySelectorAll("[data-variant]").forEach(button => button.addEventListener("click", () => {
    variant = button.dataset.variant;
    document.querySelectorAll("[data-variant]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    reload();
  }));
  document.querySelector("#theme").addEventListener("click", event => {
    theme = theme === "dark" ? "bright" : "dark";
    event.currentTarget.textContent = theme === "dark" ? "Bright mode" : "Dark mode";
    reload();
  });
  document.querySelector("#width").addEventListener("change", event => {
    frame.style.width = event.target.value === "full" ? "100%" : event.target.value + "px";
    reload();
  });
  document.querySelector("#restart").addEventListener("click", reload);
  addEventListener("message", event => {
    if (event.source === frame.contentWindow && event.origin === location.origin && Number.isFinite(event.data?.height)) {
      frame.style.height = Math.max(700, Math.min(1800, event.data.height + 8)) + "px";
    }
  });
})();
