"use strict";
// A small, dependency-free version of the connected particles on Harper's site.
(() => {
  const canvas = document.createElement("canvas");
  canvas.id = "ambient-background";
  canvas.setAttribute("aria-hidden", "true");
  document.body.prepend(canvas);
  const ctx = canvas.getContext("2d");
  if (!ctx) { canvas.remove(); return; }
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "background-toggle";
  toggle.textContent = "Pause background";
  toggle.setAttribute("aria-pressed", "false");
  document.body.append(toggle);
  let width = 0, height = 0, points = [], frame = 0, lastTime = 0, paused = false;

  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    const scale = Math.min(window.devicePixelRatio || 1, 1.5);
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    const count = Math.max(18, Math.min(78, Math.round(width * height / 18000)));
    points = Array.from({ length: count }, () => ({
      x: Math.random() * width, y: Math.random() * height,
      vx: (Math.random() - .5) * .24, vy: (Math.random() - .5) * .24,
      radius: 1 + Math.random() * 1.8,
    }));
    draw(0);
  }

  function draw(step) {
    ctx.clearRect(0, 0, width, height);
    for (const point of points) {
      point.x = (point.x + point.vx * step + width) % width;
      point.y = (point.y + point.vy * step + height) % height;
      ctx.beginPath();
      ctx.arc(point.x, point.y, point.radius, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(184, 119, 230, .40)";
      ctx.fill();
    }
    for (let i = 0; i < points.length; i++) {
      for (let j = i + 1; j < points.length; j++) {
        const a = points[i], b = points[j];
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        if (distance >= 136) continue;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = `rgba(177, 105, 208, ${.22 * (1 - distance / 136)})`;
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }
  }

  function animate(now) {
    if (now - lastTime >= 1000 / 30) {
      draw(lastTime ? Math.min((now - lastTime) / 16.67, 3) : 0);
      lastTime = now;
    }
    frame = requestAnimationFrame(animate);
  }

  function sync() {
    cancelAnimationFrame(frame);
    lastTime = 0;
    toggle.hidden = reduced.matches;
    document.body.classList.toggle("motion-paused", paused || reduced.matches);
    if (!paused && !reduced.matches && !document.hidden) frame = requestAnimationFrame(animate);
  }
  toggle.addEventListener("click", () => {
    paused = !paused;
    toggle.textContent = paused ? "Resume background" : "Pause background";
    toggle.setAttribute("aria-pressed", String(paused));
    sync();
  });
  window.addEventListener("resize", resize);
  document.addEventListener("visibilitychange", sync);
  reduced.addEventListener("change", sync);
  resize();
  sync();
})();
