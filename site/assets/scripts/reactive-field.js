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
    const density = Math.floor(width * height / 26000);
    const count = Math.max(28, Math.min(72, density));
    particles = Array.from({ length: count }, (_, index) => ({
      x: random() * width,
      y: random() * height,
      depth: .28 + random() * .72,
      size: .45 + random() * 1.15,
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
    let x = particle.x + pointer.easedX * particle.depth * 15;
    let y = particle.y + pointer.easedY * particle.depth * 10;
    x += Math.sin(time * .00016 + particle.phase) * 2.5 * particle.depth;
    y += Math.cos(time * .00012 + particle.phase) * 2 * particle.depth;

    if (pointer.active) {
      const offsetX = x - pointer.x;
      const offsetY = y - pointer.y;
      const distance = Math.hypot(offsetX, offsetY) || 1;
      const reach = 190;

      if (distance < reach) {
        const lens = (1 - distance / reach) * 18 * particle.depth;
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

    const glow = context.createRadialGradient(pointer.x, pointer.y, 0, pointer.x, pointer.y, 230);
    glow.addColorStop(0, "rgba(101, 229, 255, .075)");
    glow.addColorStop(.48, "rgba(255, 99, 200, .035)");
    glow.addColorStop(1, "rgba(3, 8, 17, 0)");
    context.fillStyle = glow;
    context.fillRect(pointer.x - 230, pointer.y - 230, 460, 460);
  };

  const draw = (time) => {
    if (!visible) {
      return;
    }

    pointer.easedX += ((pointer.active ? pointer.x / width - .5 : 0) - pointer.easedX) * .045;
    pointer.easedY += ((pointer.active ? pointer.y / height - .5 : 0) - pointer.easedY) * .045;

    context.clearRect(0, 0, width, height);
    drawGlow();

    const positions = particles.map((particle) => positionParticle(particle, time));

    if (pointer.active) {
      positions.forEach((point) => {
        const distance = Math.hypot(point.x - pointer.x, point.y - pointer.y);

        if (distance < 165) {
          context.beginPath();
          context.moveTo(pointer.x, pointer.y);
          context.lineTo(point.x, point.y);
          context.strokeStyle = `rgba(255, 99, 200, ${(1 - distance / 165) * .16})`;
          context.lineWidth = .7;
          context.stroke();
        }
      });
    }

    for (let first = 0; first < positions.length; first += 1) {
      for (let second = first + 1; second < positions.length; second += 1) {
        const distance = Math.hypot(
          positions[first].x - positions[second].x,
          positions[first].y - positions[second].y,
        );

        if (distance < 108) {
          context.beginPath();
          context.moveTo(positions[first].x, positions[first].y);
          context.lineTo(positions[second].x, positions[second].y);
          context.strokeStyle = `rgba(101, 229, 255, ${(1 - distance / 108) * .085})`;
          context.lineWidth = .6;
          context.stroke();
        }
      }
    }

    particles.forEach((particle, index) => {
      const point = positions[index];
      const pulse = .58 + Math.sin(time * .001 + particle.phase) * .22;
      context.beginPath();
      context.arc(point.x, point.y, particle.size, 0, Math.PI * 2);
      context.fillStyle = particle.accent
        ? `rgba(255, 99, 200, ${pulse})`
        : `rgba(185, 243, 255, ${pulse * .72})`;
      context.fill();
    });

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
