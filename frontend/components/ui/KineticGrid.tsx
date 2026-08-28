"use client";

import { useCallback, useEffect, useRef, type ReactNode } from "react";
import { useTheme } from "next-themes";

import { cn } from "@/lib/utils";

/**
 * A grid that bends around the cursor, with ripples on click.
 *
 * Three things differ from a drop-in canvas background, all forced by this app:
 *
 * 1. It paints no background of its own, so it reads as a texture over `--ink`
 *    rather than a black plate that would survive into the light theme. Resting
 *    ink and the accent are both theme-derived.
 * 2. It sizes and listens on its own container, not the window, so it is a
 *    section background rather than a fixed layer under the whole page.
 * 3. It respects `prefers-reduced-motion` (one static frame, no listeners) and
 *    stops animating once scrolled out of view.
 */

interface Point {
  x: number;
  y: number;
}

interface Ripple {
  x: number;
  y: number;
  radius: number;
  opacity: number;
  born: number;
}

const CELL_SIZE = 55;
const INFLUENCE_RADIUS = 260;
const MAX_WARP = 24;
const DOT_SPACING = 28;
const LERP_SPEED = 0.08;

const NODE_BASE_RADIUS = 1.8;
const NODE_ACTIVE_RADIUS = 3.2;

const OFFSCREEN = -9999;

interface Rgb {
  r: number;
  g: number;
  b: number;
}

/**
 * Two colours, not one.
 *
 * The grid at rest is theme ink and stays out of the way. Only the cells the
 * cursor is actually bending light up, interpolating ink → accent across the
 * same falloff that drives the warp, so the colour marks exactly the region
 * being deformed. Alphas differ per theme because dark ink on a light page
 * carries further than light ink on a dark one at the same opacity.
 */
const INK = {
  dark: {
    base: { r: 255, g: 255, b: 255 },
    accent: { r: 74, g: 158, b: 255 },
    line: 0.13,
    lineActive: 0.9,
    node: 0.2,
    nodeActive: 1,
  },
  light: {
    base: { r: 10, g: 10, b: 12 },
    // Deeper than the dark-theme accent: #4A9EFF on white is too pale to read.
    accent: { r: 29, g: 110, b: 225 },
    line: 0.1,
    lineActive: 0.85,
    node: 0.16,
    nodeActive: 0.95,
  },
};

function lerpN(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

/** Cubic smoothstep, used to keep the falloff from looking linear. */
function smooth(t: number) {
  return t * t * (3 - 2 * t);
}

/** Interpolates colour and alpha together, so ink fades into accent as one move. */
function lerpColor(base: Rgb, accent: Rgb, baseA: number, accentA: number, t: number) {
  const r = Math.round(lerpN(base.r, accent.r, t));
  const g = Math.round(lerpN(base.g, accent.g, t));
  const b = Math.round(lerpN(base.b, accent.b, t));
  return `rgba(${r},${g},${b},${lerpN(baseA, accentA, t).toFixed(3)})`;
}

function rgba({ r, g, b }: Rgb, alpha: number) {
  return `rgba(${r},${g},${b},${alpha.toFixed(3)})`;
}

export default function KineticGrid({
  children,
  className,
  accent = true,
}: {
  children?: ReactNode;
  className?: string;
  /** Light the warped cells in the accent colour. Off leaves the grid all ink. */
  accent?: boolean;
}) {
  const { resolvedTheme } = useTheme();
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  const mouseRef = useRef<Point>({ x: OFFSCREEN, y: OFFSCREEN });
  const targetMouseRef = useRef<Point>({ x: OFFSCREEN, y: OFFSCREEN });
  const ripplesRef = useRef<Ripple[]>([]);
  const rafRef = useRef<number>(0);
  const sizeRef = useRef<{ w: number; h: number }>({ w: 0, h: 0 });
  // Read inside the animation loop, which must not be torn down on every
  // theme flip just to change a colour.
  const inkRef = useRef(INK.dark);

  const accentRef = useRef(accent);

  useEffect(() => {
    inkRef.current = resolvedTheme === "light" ? INK.light : INK.dark;
  }, [resolvedTheme]);

  useEffect(() => {
    accentRef.current = accent;
  }, [accent]);

  // ── Warp ──────────────────────────────────────────────────────

  const getWarpedPoint = useCallback(
    (
      gx: number,
      gy: number,
      col: number,
      row: number,
      mouse: Point,
      ripples: Ripple[],
      cols: number,
      rows: number,
    ): { pt: Point; proximity: number } => {
      // Pins the boundary rows and columns so the grid cannot peel away from
      // the edges of the section it is filling.
      const edgeMargin = 1.5;
      const colPin = Math.min(col / edgeMargin, (cols - 1 - col) / edgeMargin, 1);
      const rowPin = Math.min(row / edgeMargin, (rows - 1 - row) / edgeMargin, 1);
      const pinFactor = colPin * colPin * rowPin * rowPin;

      const dx = gx - mouse.x;
      const dy = gy - mouse.y;
      const dist = Math.sqrt(dx * dx + dy * dy);
      const proximity = Math.max(0, 1 - dist / INFLUENCE_RADIUS) * pinFactor;

      let rx = 0;
      let ry = 0;
      for (const ripple of ripples) {
        const rdx = gx - ripple.x;
        const rdy = gy - ripple.y;
        const rdist = Math.sqrt(rdx * rdx + rdy * rdy);
        const waveWidth = 55;
        const diff = rdist - ripple.radius;
        if (Math.abs(diff) < waveWidth) {
          const strength =
            (1 - Math.abs(diff) / waveWidth) * ripple.opacity * 18 * pinFactor;
          const angle = Math.atan2(rdy, rdx);
          const sign = diff < 0 ? -1 : 1;
          rx += Math.cos(angle) * strength * sign * -1;
          ry += Math.sin(angle) * strength * sign * -1;
        }
      }

      if (dist < INFLUENCE_RADIUS && dist > 0 && pinFactor > 0) {
        const t = dist / INFLUENCE_RADIUS;
        const eased = t < 0.01 ? 0 : (1 - t) * (1 - t) * Math.min(1, dist / 60);
        const warpAmt = eased * MAX_WARP * pinFactor;
        const angle = Math.atan2(dy, dx);
        return {
          pt: {
            x: gx - Math.cos(angle) * warpAmt + rx,
            y: gy - Math.sin(angle) * warpAmt + ry,
          },
          proximity,
        };
      }

      return { pt: { x: gx + rx, y: gy + ry }, proximity };
    },
    [],
  );

  // ── Draw ──────────────────────────────────────────────────────

  const draw = useCallback(
    (now: number) => {
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext("2d");
      if (!canvas || !ctx) return;

      const { w: W, h: H } = sizeRef.current;
      if (W === 0 || H === 0) return;

      const mouse = mouseRef.current;
      const ripples = ripplesRef.current;
      const ink = inkRef.current;
      // With the accent off the "active" colour is just ink, so every lerp
      // below collapses to an alpha ramp and the grid stays monochrome.
      const hot = accentRef.current ? ink.accent : ink.base;

      // Transparent: the section's own --ink shows through, so this works in
      // both themes and never fights the page background.
      ctx.clearRect(0, 0, W, H);

      ctx.fillStyle = rgba(ink.base, 0.05);
      for (let x = DOT_SPACING / 2; x < W; x += DOT_SPACING) {
        for (let y = DOT_SPACING / 2; y < H; y += DOT_SPACING) {
          ctx.beginPath();
          ctx.arc(x, y, 0.7, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      for (let i = ripples.length - 1; i >= 0; i--) {
        const ripple = ripples[i];
        const age = (now - ripple.born) / 1000;
        ripple.radius = Math.max(0, age * 400);
        ripple.opacity = Math.max(0, 1 - age * 1.2);
        if (ripple.opacity <= 0) ripples.splice(i, 1);
      }

      const cols = Math.max(2, Math.ceil(W / CELL_SIZE)) + 1;
      const rows = Math.max(2, Math.ceil(H / CELL_SIZE)) + 1;
      const cellW = W / (cols - 1);
      const cellH = H / (rows - 1);

      const pts: Point[][] = [];
      const prox: number[][] = [];

      for (let row = 0; row < rows; row++) {
        pts[row] = [];
        prox[row] = [];
        for (let col = 0; col < cols; col++) {
          const { pt, proximity } = getWarpedPoint(
            col * cellW,
            row * cellH,
            col,
            row,
            mouse,
            ripples,
            cols,
            rows,
          );
          pts[row][col] = pt;
          prox[row][col] = proximity;
        }
      }

      const drawSeg = (p1: Point, p2: Point, pr1: number, pr2: number) => {
        const t = smooth((pr1 + pr2) / 2);
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.strokeStyle = lerpColor(ink.base, hot, ink.line, ink.lineActive, t);
        ctx.lineWidth = lerpN(0.8, 1.5, t);
        ctx.stroke();
      };

      ctx.lineCap = "butt";

      for (let row = 0; row < rows; row++) {
        for (let col = 0; col < cols - 1; col++) {
          drawSeg(pts[row][col], pts[row][col + 1], prox[row][col], prox[row][col + 1]);
        }
      }
      for (let col = 0; col < cols; col++) {
        for (let row = 0; row < rows - 1; row++) {
          drawSeg(pts[row][col], pts[row + 1][col], prox[row][col], prox[row + 1][col]);
        }
      }

      for (let row = 0; row < rows; row++) {
        for (let col = 0; col < cols; col++) {
          const p = pts[row][col];
          const t = smooth(prox[row][col]);
          const radius = lerpN(NODE_BASE_RADIUS, NODE_ACTIVE_RADIUS, t);

          if (t > 0.3) {
            const glowR = radius + lerpN(0, 6, (t - 0.3) / 0.7);
            const gradient = ctx.createRadialGradient(
              p.x,
              p.y,
              radius * 0.5,
              p.x,
              p.y,
              glowR,
            );
            gradient.addColorStop(0, rgba(hot, t * 0.3));
            gradient.addColorStop(1, rgba(hot, 0));
            ctx.beginPath();
            ctx.arc(p.x, p.y, glowR, 0, Math.PI * 2);
            ctx.fillStyle = gradient;
            ctx.fill();
          }

          ctx.beginPath();
          ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
          ctx.fillStyle = lerpColor(ink.base, hot, ink.node, ink.nodeActive, t);
          ctx.fill();
        }
      }

      for (const ripple of ripples) {
        ctx.beginPath();
        ctx.arc(ripple.x, ripple.y, Math.max(0, ripple.radius), 0, Math.PI * 2);
        ctx.strokeStyle = rgba(hot, ripple.opacity * 0.28);
        ctx.lineWidth = 1.5;
        ctx.stroke();
      }
    },
    [getWarpedPoint],
  );

  // ── Lifecycle ─────────────────────────────────────────────────

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const setSize = () => {
      const { width, height } = container.getBoundingClientRect();
      if (width === 0 || height === 0) return;
      // Back the canvas at device resolution; a 1:1 buffer is visibly soft on
      // any HiDPI screen. Capped at 2 so a 3x phone does not pay triple.
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      canvas.getContext("2d")?.setTransform(dpr, 0, 0, dpr, 0, 0);
      sizeRef.current = { w: width, h: height };
      draw(performance.now());
    };

    setSize();
    const resizeObserver = new ResizeObserver(setSize);
    resizeObserver.observe(container);

    // A static grid is the whole effect for anyone who asked for less motion.
    if (reduceMotion) {
      return () => resizeObserver.disconnect();
    }

    const animate = (now: number) => {
      const m = mouseRef.current;
      const t = targetMouseRef.current;
      m.x = lerpN(m.x, t.x, LERP_SPEED);
      m.y = lerpN(m.y, t.y, LERP_SPEED);
      draw(now);
      rafRef.current = requestAnimationFrame(animate);
    };

    const start = () => {
      if (rafRef.current === 0) rafRef.current = requestAnimationFrame(animate);
    };
    const stop = () => {
      if (rafRef.current !== 0) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
      }
    };

    const onPointerMove = (event: PointerEvent) => {
      const rect = container.getBoundingClientRect();
      const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      // Snap on first entry: easing in from off-canvas drags a visible wave
      // across the grid before it settles.
      if (targetMouseRef.current.x === OFFSCREEN) mouseRef.current = { ...point };
      targetMouseRef.current = point;
    };

    const onPointerLeave = () => {
      targetMouseRef.current = { x: OFFSCREEN, y: OFFSCREEN };
    };

    const onClick = (event: MouseEvent) => {
      const rect = container.getBoundingClientRect();
      ripplesRef.current.push({
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
        radius: 0,
        opacity: 1,
        born: performance.now(),
      });
    };

    // Scoped to the section: no window listeners, so nothing runs on any other
    // page and clicks elsewhere cannot spawn ripples here.
    container.addEventListener("pointermove", onPointerMove);
    container.addEventListener("pointerleave", onPointerLeave);
    container.addEventListener("click", onClick);

    // Nothing worth animating once it is scrolled past or the tab is hidden.
    const visibility = new IntersectionObserver(
      ([entry]) => (entry.isIntersecting ? start() : stop()),
      { threshold: 0 },
    );
    visibility.observe(container);

    const onVisibilityChange = () => {
      if (document.hidden) stop();
      else if (container.getBoundingClientRect().bottom > 0) start();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);

    return () => {
      stop();
      resizeObserver.disconnect();
      visibility.disconnect();
      container.removeEventListener("pointermove", onPointerMove);
      container.removeEventListener("pointerleave", onPointerLeave);
      container.removeEventListener("click", onClick);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [draw]);

  return (
    <div ref={containerRef} className={cn("relative overflow-hidden", className)}>
      <canvas
        ref={canvasRef}
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 z-0 h-full w-full"
      />
      <div className="relative z-10">{children}</div>
    </div>
  );
}
