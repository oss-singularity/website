(() => {
  "use strict";

  const menus = Array.from(document.querySelectorAll(".guide-toc"), (nav) => {
    const items = Array.from(nav.querySelectorAll('a[href^="#"]'), (link) => {
      try {
        const section = document.getElementById(decodeURIComponent(link.hash.slice(1)));
        return section ? { link, section } : null;
      } catch {
        return null;
      }
    }).filter(Boolean);
    return { items, current: null };
  }).filter((menu) => menu.items.length);
  if (!menus.length) return;

  let frame = 0;
  function update() {
    frame = 0;
    const height = window.innerHeight;
    const readingLine = Math.min(180, Math.max(72, height * 0.25));
    const atBottom = window.scrollY + height >= document.documentElement.scrollHeight - 2;

    for (const menu of menus) {
      let current = null;
      for (const item of menu.items) {
        const bounds = item.section.getBoundingClientRect();
        if (bounds.top <= readingLine && bounds.bottom > readingLine) current = item;
      }
      // A short final section may never reach the reading line.
      const last = menu.items[menu.items.length - 1];
      const lastBounds = last.section.getBoundingClientRect();
      if (atBottom && lastBounds.top < height && lastBounds.bottom > 0) current = last;
      if (current === menu.current) continue;
      menu.current?.link.removeAttribute("aria-current");
      current?.link.setAttribute("aria-current", "location");
      menu.current = current;
    }
  }

  function schedule() {
    if (!frame) frame = window.requestAnimationFrame(update);
  }
  window.addEventListener("scroll", schedule, { passive: true });
  window.addEventListener("resize", schedule);
  window.addEventListener("hashchange", schedule);
  window.addEventListener("pageshow", schedule);
  window.addEventListener("load", schedule);
  window.addEventListener("pagehide", () => {
    window.cancelAnimationFrame(frame);
    frame = 0;
  });
  if ("ResizeObserver" in window) {
    const observer = new ResizeObserver(schedule);
    for (const menu of menus) {
      for (const item of menu.items) observer.observe(item.section);
    }
  }
  schedule();
})();
