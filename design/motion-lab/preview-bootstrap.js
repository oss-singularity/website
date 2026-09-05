(() => {
  "use strict";
  const params = new URLSearchParams(location.search);
  document.documentElement.dataset.theme = params.get("theme") === "bright" ? "bright" : "dark";
  // Lab-only seed: all variants share the same core path and segment durations.
  let seed = 60;
  Math.random = () => {
    seed |= 0; seed = seed + 0x6D2B79F5 | 0;
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
  addEventListener("DOMContentLoaded", () => {
    const variant = params.get("variant");
    if (["thin-energy", "wide-wormhole"].includes(variant)) {
      window.labStreams = window.ObservatoryStreams.mount(document.querySelector(".constellation"), {variant, seed: 60});
    }
    const content = document.querySelector("main");
    // Measure content, not viewport-sized body/document scrollHeight: the latter
    // feeds each parent resize back into the next iframe-height measurement.
    const resize = new ResizeObserver(() => {
      parent.postMessage({height: Math.ceil(content.getBoundingClientRect().bottom + 24)}, location.origin);
    });
    resize.observe(content);
  });
})();
