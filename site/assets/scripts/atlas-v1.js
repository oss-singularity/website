(() => {
  "use strict";
  const controls = document.querySelector(".atlas-controls");
  if (!controls) return;
  const entries = [...document.querySelectorAll(".atlas-entry")];
  const search = document.querySelector("#atlas-search");
  const filters = [...document.querySelectorAll("[data-filter]")];
  const count = document.querySelector("#atlas-count");
  const empty = document.querySelector("#atlas-empty");
  const panel = document.querySelector("#atlas-compare");
  const comparison = document.querySelector("#compare-content");
  const checkboxes = [...document.querySelectorAll(".compare-label input")];
  let category = "all";
  const params = new URLSearchParams(location.search);
  if (filters.some(button => button.dataset.filter === params.get("category"))) category = params.get("category");
  search.value = (params.get("q") || "").slice(0, 200);
  function filter(updateURL = true) {
    const query = search.value.trim().toLocaleLowerCase();
    entries.forEach(entry => {
      entry.hidden = !(category === "all" || entry.dataset.category === category) || !entry.textContent.toLocaleLowerCase().includes(query);
    });
    filters.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.filter === category)));
    const visible = entries.filter(entry => !entry.hidden).length;
    count.textContent = `${visible} of ${entries.length} entries`;
    empty.hidden = visible !== 0;
    if (updateURL) {
      const url = new URL(location.href);
      if (category === "all") url.searchParams.delete("category"); else url.searchParams.set("category", category);
      if (search.value.trim()) url.searchParams.set("q", search.value.trim()); else url.searchParams.delete("q");
      history.replaceState(null, "", url);
    }
  }
  filters.forEach(button => button.addEventListener("click", () => { category = button.dataset.filter; filter(); }));
  search.addEventListener("input", () => filter());
  document.querySelector("#atlas-reset").addEventListener("click", () => { category = "all"; search.value = ""; filter(); search.focus(); });
  document.querySelector("#atlas-surprise").addEventListener("click", () => {
    const visible = entries.filter(entry => !entry.hidden);
    if (!visible.length) { search.focus(); return; }
    const entry = visible[Math.floor(Math.random() * visible.length)];
    entry.querySelector("h2").setAttribute("tabindex", "-1");
    entry.querySelector("h2").focus({ preventScroll: true });
    entry.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  });
  function compare() {
    const selected = checkboxes.filter(input => input.checked).map(input => entries.find(entry => entry.id === input.value));
    panel.hidden = selected.length === 0;
    comparison.replaceChildren();
    checkboxes.forEach(input => { input.disabled = selected.length >= 3 && !input.checked; });
    document.querySelector("#compare-status").textContent = `${selected.length} of 3 comparison places selected.`;
    if (!selected.length) return;
    const table = document.createElement("table");
    const caption = document.createElement("caption");
    caption.className = "sr-only";
    caption.textContent = "Selected ecosystem projects compared by purpose, use case and license";
    table.append(caption);
    const rows = [["Project", "h2"], ["Purpose", ".entry-body > p"], ["Useful for", ".entry-fit > p"], ["License", ".entry-license"]];
    rows.forEach(([label, selector], index) => {
      const row = document.createElement("tr");
      const heading = document.createElement("th");
      heading.scope = "row";
      heading.textContent = label;
      row.append(heading);
      selected.forEach(entry => {
        const cell = document.createElement(index === 0 ? "th" : "td");
        if (index === 0) cell.scope = "col";
        cell.textContent = entry.querySelector(selector).textContent;
        row.append(cell);
      });
      table.append(row);
    });
    comparison.append(table);
  }
  checkboxes.forEach(input => { input.closest("label").hidden = false; input.addEventListener("change", compare); });
  document.querySelector("#compare-clear").addEventListener("click", () => { checkboxes.forEach(input => { input.checked = false; }); compare(); search.focus(); });
  controls.hidden = false;
  filter(false);
})();
