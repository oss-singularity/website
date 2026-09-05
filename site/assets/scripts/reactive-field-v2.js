(() => {
  const canvas = document.querySelector("[data-signal-field]");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  if (!(canvas instanceof HTMLCanvasElement) || reducedMotion.matches) {
    return;
  }

  const context = canvas.getContext("2d", { alpha: true });

  if (!context) {
    return;
  }

  const pointer = { active: false, x: 0, y: 0, easedX: 0, easedY: 0 };
  let particles = [];
  let width = 0;
  let height = 0;
  let pixelRatio = 1;
  let frame = 0;
  let visible = true;
  let palette;
  const updatePalette = () => {
    palette = document.documentElement.dataset.theme === "bright"
      ? { cyan: "0, 104, 128", pink: "165, 27, 117", point: "28, 100, 130", composite: "source-over" }
      : { cyan: "101, 229, 255", pink: "255, 99, 200", point: "185, 243, 255", composite: "lighter" };
  };
  updatePalette();
  document.addEventListener("oss-theme-change", updatePalette);

  const random = (() => {
    let seed = 0x51a1f13d;
    return () => {
      seed |= 0;
      seed = seed + 0x6d2b79f5 | 0;
      let value = Math.imul(seed ^ seed >>> 15, 1 | seed);
      value = value + Math.imul(value ^ value >>> 7, 61 | value) ^ value;
      return ((value ^ value >>> 14) >>> 0) / 4294967296;
    };
  })();

  const createParticles = () => {
    const density = Math.floor(width * height / 15000);
    const count = Math.max(48, Math.min(96, density));
    particles = Array.from({ length: count }, (_, index) => ({
      x: random() * width,
      y: random() * height,
      depth: .28 + random() * .72,
      size: .65 + random() * 1.4,
      phase: random() * Math.PI * 2,
      accent: index % 7 === 0,
    }));
  };

  const resize = () => {
    width = window.innerWidth;
    height = window.innerHeight;
    pixelRatio = Math.min(window.devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(width * pixelRatio);
    canvas.height = Math.round(height * pixelRatio);
    context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
    createParticles();
  };

  const positionParticle = (particle, time) => {
    let x = particle.x + pointer.easedX * particle.depth * 28;
    let y = particle.y + pointer.easedY * particle.depth * 20;
    x += Math.sin(time * .00016 + particle.phase) * 4 * particle.depth;
    y += Math.cos(time * .00012 + particle.phase) * 3 * particle.depth;

    if (pointer.active) {
      const offsetX = x - pointer.x;
      const offsetY = y - pointer.y;
      const distance = Math.hypot(offsetX, offsetY) || 1;
      const reach = 270;

      if (distance < reach) {
        const lens = (1 - distance / reach) * 48 * particle.depth;
        x += offsetX / distance * lens;
        y += offsetY / distance * lens;
      }
    }

    return { x, y };
  };

  const drawGlow = () => {
    if (!pointer.active) {
      return;
    }

    const glow = context.createRadialGradient(pointer.x, pointer.y, 0, pointer.x, pointer.y, 300);
    glow.addColorStop(0, `rgba(${palette.cyan}, .16)`);
    glow.addColorStop(.48, `rgba(${palette.pink}, .08)`);
    glow.addColorStop(1, "rgba(3, 8, 17, 0)");
    context.fillStyle = glow;
    context.fillRect(pointer.x - 300, pointer.y - 300, 600, 600);
  };

  const drawPointerSignal = (time) => {
    if (!pointer.active) {
      return;
    }

    context.save();
    context.translate(pointer.x, pointer.y);
    context.rotate(time * .00022);
    context.setLineDash([3, 9]);
    context.lineCap = "round";

    context.beginPath();
    context.arc(0, 0, 58, -.2, Math.PI * 1.18);
    context.strokeStyle = `rgba(${palette.cyan}, .48)`;
    context.lineWidth = 1.1;
    context.stroke();

    context.rotate(-time * .00046);
    context.beginPath();
    context.arc(0, 0, 86, .35, Math.PI * 1.45);
    context.strokeStyle = `rgba(${palette.pink}, .38)`;
    context.lineWidth = .9;
    context.stroke();

    context.setLineDash([]);
    context.beginPath();
    context.arc(0, 0, 4, 0, Math.PI * 2);
    context.strokeStyle = `rgba(${palette.point}, .72)`;
    context.lineWidth = 1;
    context.stroke();
    context.restore();
  };

  const draw = (time) => {
    if (!visible) {
      return;
    }

    pointer.easedX += ((pointer.active ? pointer.x / width - .5 : 0) - pointer.easedX) * .075;
    pointer.easedY += ((pointer.active ? pointer.y / height - .5 : 0) - pointer.easedY) * .075;

    context.clearRect(0, 0, width, height);
    context.save();
    context.globalCompositeOperation = palette.composite;
    drawGlow();

    const positions = particles.map((particle) => positionParticle(particle, time));

    if (pointer.active) {
      positions.forEach((point, index) => {
        const distance = Math.hypot(point.x - pointer.x, point.y - pointer.y);

        if (distance < 260) {
          context.beginPath();
          context.moveTo(pointer.x, pointer.y);
          context.lineTo(point.x, point.y);
          const opacity = (1 - distance / 260) * .44;
          context.strokeStyle = particles[index].accent
            ? `rgba(${palette.pink}, ${opacity})`
            : `rgba(${palette.cyan}, ${opacity})`;
          context.lineWidth = 1;
          context.stroke();
        }
      });
    }

    drawPointerSignal(time);

    for (let first = 0; first < positions.length; first += 1) {
      for (let second = first + 1; second < positions.length; second += 1) {
        const distance = Math.hypot(
          positions[first].x - positions[second].x,
          positions[first].y - positions[second].y,
        );

        if (distance < 145) {
          context.beginPath();
          context.moveTo(positions[first].x, positions[first].y);
          context.lineTo(positions[second].x, positions[second].y);
          context.strokeStyle = `rgba(${palette.cyan}, ${(1 - distance / 145) * .19})`;
          context.lineWidth = .75;
          context.stroke();
        }
      }
    }

    particles.forEach((particle, index) => {
      const point = positions[index];
      const pulse = .7 + Math.sin(time * .001 + particle.phase) * .25;
      context.beginPath();
      context.arc(point.x, point.y, particle.size, 0, Math.PI * 2);
      context.fillStyle = particle.accent
        ? `rgba(${palette.pink}, ${pulse})`
        : `rgba(${palette.point}, ${pulse * .88})`;
      context.fill();
    });

    context.restore();

    frame = window.requestAnimationFrame(draw);
  };

  const updatePointer = (event) => {
    if (event.pointerType === "touch" && !event.isPrimary) {
      return;
    }
    pointer.active = true;
    pointer.x = event.clientX;
    pointer.y = event.clientY;
  };

  const releasePointer = () => {
    pointer.active = false;
  };

  const updateVisibility = () => {
    visible = !document.hidden;
    window.cancelAnimationFrame(frame);
    if (visible) {
      frame = window.requestAnimationFrame(draw);
    }
  };

  window.addEventListener("resize", resize, { passive: true });
  window.addEventListener("pointermove", updatePointer, { passive: true });
  window.addEventListener("pointerdown", updatePointer, { passive: true });
  window.addEventListener("pointerup", releasePointer, { passive: true });
  document.documentElement.addEventListener("pointerleave", releasePointer, { passive: true });
  document.addEventListener("visibilitychange", updateVisibility);

  resize();
  frame = window.requestAnimationFrame(draw);
})();
