/* Shared, context-aware Color & Look module (patchReuseColoAudio §5/§6/§15).

   One implementation of the Color & Look canvas preview pipeline + control
   binding, reused by:
     - Single Clip (video_effects.js delegates its pipeline to WVGColorLook.pipeline)
     - VideoSequenceQueue Global Color & Look (context "sequence_global")
     - VideoSequenceQueue per-clip Custom look (context "sequence_clip_override")

   Reads/writes the canonical VideoEffects schema (app/models/project_models.py).
   Root-scoped via data-color-look-context + data-path/data-field — no global IDs. */

window.WVGColorLook = (function () {
  "use strict";

  /* ---------- canonical VideoEffects default (matches the pydantic model) ---------- */
  function defaults() {
    return {
      enabled: false, saturation: 1.0, contrast: 1.0, hue: 0.0, temperature: 0.0,
      shadows: 0.0, highlights: 0.0, brightness: 0.0, gamma: 1.0,
      vignette: { enabled: false, intensity: 0.25, radius: 0.75, softness: 0.5 },
      film_grain: { enabled: false, intensity: 0.15, grain_size: 0.5, animated: true },
      sharpness: { enabled: false, amount: 1.0 },
      vhs_effect: { enabled: false, intensity: 0.3, scanlines: 0.2, chromatic_aberration: 0.15,
        noise: 0.15, jitter: 0.1, tracking_distortion: 0.1, color_bleeding: 0.15, tape_damage: 0.05 }
    };
  }

  function getPath(obj, path) {
    return path.split(".").reduce(function (o, k) { return o ? o[k] : undefined; }, obj);
  }
  function setPath(obj, path, value) {
    var keys = path.split(".");
    var target = obj;
    for (var i = 0; i < keys.length - 1; i++) {
      if (target[keys[i]] == null || typeof target[keys[i]] !== "object") target[keys[i]] = {};
      target = target[keys[i]];
    }
    target[keys[keys.length - 1]] = value;
  }

  /* ===================================================================
     Canvas effect pipeline (ported verbatim from video_effects.js so the
     Single Clip preview and the sequence preview are pixel-identical).
     =================================================================== */
  function pixelPass(fx, d, w) {
    var sat = fx.saturation, con = fx.contrast, bri = fx.brightness / 100 * 128;
    var gam = 1 / fx.gamma, temp = fx.temperature, hue = fx.hue * Math.PI / 180;
    var sh = fx.shadows / 100, hi = fx.highlights / 100;
    var cosH = Math.cos(hue), sinH = Math.sin(hue);
    var caShift = 0;
    if (fx.vhs_effect.enabled && fx.vhs_effect.intensity > 0) {
      caShift = Math.round(fx.vhs_effect.chromatic_aberration * fx.vhs_effect.intensity * 8);
    }
    var gammaLUT = new Uint8ClampedArray(256);
    for (var i = 0; i < 256; i++) gammaLUT[i] = 255 * Math.pow(i / 255, gam);
    var src = caShift ? new Uint8ClampedArray(d) : null;

    for (var p = 0; p < d.length; p += 4) {
      var r = d[p], g = d[p + 1], b = d[p + 2];
      if (caShift) {
        var x = (p / 4) % w;
        var xr = Math.min(w - 1, x + caShift), xb = Math.max(0, x - caShift);
        var row = p - x * 4;
        r = src[row + xr * 4];
        b = src[row + xb * 4 + 2];
      }
      r = gammaLUT[r]; g = gammaLUT[g]; b = gammaLUT[b];
      r = (r - 128) * con + 128 + bri; g = (g - 128) * con + 128 + bri; b = (b - 128) * con + 128 + bri;
      if (temp) { r += temp * 0.3; b -= temp * 0.3; }
      if (hue) {
        var Y = 0.299 * r + 0.587 * g + 0.114 * b;
        var I = 0.596 * r - 0.274 * g - 0.322 * b;
        var Q = 0.211 * r - 0.523 * g + 0.312 * b;
        var I2 = I * cosH - Q * sinH, Q2 = I * sinH + Q * cosH;
        r = Y + 0.956 * I2 + 0.621 * Q2; g = Y - 0.272 * I2 - 0.647 * Q2; b = Y - 1.106 * I2 + 1.703 * Q2;
      }
      var lum = 0.299 * r + 0.587 * g + 0.114 * b;
      if (sat !== 1) { r = lum + (r - lum) * sat; g = lum + (g - lum) * sat; b = lum + (b - lum) * sat; }
      if (sh || hi) {
        var ln = Math.min(Math.max(lum / 255, 0), 1);
        var shadowW = 1 - ln, highlightW = ln;
        var lift = sh * 46 * shadowW * shadowW + hi * 46 * highlightW * highlightW;
        r += lift; g += lift; b += lift;
      }
      d[p] = r; d[p + 1] = g; d[p + 2] = b;
    }
  }

  function sharpen(image, w, h, amount) {
    var s = image.data, copy = new Uint8ClampedArray(s);
    var a = amount * 0.55;
    for (var y = 1; y < h - 1; y++) {
      for (var x = 1; x < w - 1; x++) {
        var p = (y * w + x) * 4;
        for (var c = 0; c < 3; c++) {
          var v = copy[p + c] * (1 + 4 * a)
            - a * (copy[p - 4 + c] + copy[p + 4 + c] + copy[p - w * 4 + c] + copy[p + w * 4 + c]);
          s[p + c] = v;
        }
      }
    }
  }

  function vignetteOverlay(ctx, fx, w, h) {
    var v = fx.vignette, cx = w / 2, cy = h / 2;
    var maxR = Math.sqrt(cx * cx + cy * cy);
    var inner = maxR * (0.25 + 0.65 * v.radius);
    var outer = inner + maxR * (0.15 + 0.85 * v.softness);
    var grad = ctx.createRadialGradient(cx, cy, inner, cx, cy, outer);
    grad.addColorStop(0, "rgba(0,0,0,0)");
    grad.addColorStop(1, "rgba(0,0,0," + Math.min(v.intensity, 1) + ")");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, w, h);
  }

  function grainOverlay(ctx, fx, w, h) {
    var g = fx.film_grain;
    var size = Math.max(1, Math.round(1 + g.grain_size * 3));
    var gw = Math.ceil(w / size), gh = Math.ceil(h / size);
    var noise = ctx.createImageData(gw, gh);
    for (var p = 0; p < noise.data.length; p += 4) {
      var n = 128 + (Math.random() - 0.5) * 255;
      noise.data[p] = noise.data[p + 1] = noise.data[p + 2] = n;
      noise.data[p + 3] = 255;
    }
    var tmp = document.createElement("canvas");
    tmp.width = gw; tmp.height = gh;
    tmp.getContext("2d").putImageData(noise, 0, 0);
    ctx.save();
    ctx.globalAlpha = g.intensity * 0.55;
    ctx.globalCompositeOperation = "overlay";
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(tmp, 0, 0, gw, gh, 0, 0, w, h);
    ctx.restore();
  }

  function vhsOverlay(ctx, canvas, fx, w, h) {
    var v = fx.vhs_effect, m = v.intensity;
    if (v.jitter > 0 || v.tracking_distortion > 0) {
      var slices = 3 + Math.round(v.tracking_distortion * 5);
      for (var i = 0; i < slices; i++) {
        var sy = Math.floor(Math.random() * h);
        var sh2 = 2 + Math.floor(Math.random() * 6);
        var dx = Math.round((Math.random() - 0.5) * 2 * (v.jitter * m * 14 + v.tracking_distortion * m * 6));
        if (dx) ctx.drawImage(canvas, 0, sy, w, sh2, dx, sy, w, sh2);
      }
    }
    if (v.noise > 0 || v.tape_damage > 0) {
      ctx.save();
      ctx.globalAlpha = Math.min((v.noise * 0.5 + v.tape_damage * 0.4) * m, 1);
      for (var n = 0; n < 220 * m; n++) {
        var nx = Math.random() * w, ny = Math.random() * h;
        ctx.fillStyle = Math.random() > 0.5 ? "rgba(255,255,255,0.5)" : "rgba(0,0,0,0.5)";
        ctx.fillRect(nx, ny, 1 + Math.random() * (1 + v.tape_damage * 8), 1);
      }
      ctx.restore();
    }
    if (v.scanlines > 0) {
      ctx.save();
      ctx.fillStyle = "rgba(0,0,0," + Math.min(v.scanlines * m, 1) * 0.4 + ")";
      for (var y = 0; y < h; y += 3) ctx.fillRect(0, y, w, 1);
      ctx.restore();
    }
    if (v.color_bleeding > 0) {
      ctx.save();
      ctx.globalAlpha = Math.min(v.color_bleeding * m * 0.9, 0.7);
      ctx.filter = "blur(" + (v.color_bleeding * m * 4).toFixed(1) + "px) saturate(1.4)";
      ctx.drawImage(canvas, 0, 0);
      ctx.restore();
      ctx.filter = "none";
    }
  }

  // Render baseFrame + fx into ctx/canvas. Shared by Single Clip and sequence.
  function renderInto(ctx, canvas, baseFrame, fx) {
    if (!baseFrame) return;
    var w = canvas.width, h = canvas.height;
    var out = new ImageData(new Uint8ClampedArray(baseFrame.data), w, h);
    if (fx.enabled) {
      pixelPass(fx, out.data, w);
      if (fx.sharpness.enabled && fx.sharpness.amount > 0) sharpen(out, w, h, fx.sharpness.amount);
    }
    ctx.putImageData(out, 0, 0);
    if (fx.enabled) {
      if (fx.vhs_effect.enabled && fx.vhs_effect.intensity > 0) vhsOverlay(ctx, canvas, fx, w, h);
      if (fx.vignette.enabled && fx.vignette.intensity > 0) vignetteOverlay(ctx, fx, w, h);
      if (fx.film_grain.enabled && fx.film_grain.intensity > 0) grainOverlay(ctx, fx, w, h);
    }
    canvas.style.opacity = fx.enabled ? "1" : "0.85";
  }

  var pipeline = {
    pixelPass: pixelPass, sharpen: sharpen, vignetteOverlay: vignetteOverlay,
    grainOverlay: grainOverlay, vhsOverlay: vhsOverlay, renderInto: renderInto
  };

  function fmt(path, value) {
    var leaf = path.split(".").pop();
    if (leaf === "hue") return Math.round(value) + "°";
    if (["temperature", "shadows", "highlights", "brightness"].indexOf(leaf) !== -1) return String(Math.round(value));
    return Number(value).toFixed(2);
  }

  /* ===================================================================
     Context-aware component (sequence contexts)
     =================================================================== */
  function mount(root, opts) {
    if (!root) return null;
    opts = opts || {};
    var onChange = opts.onChange || function () {};
    var previewSource = opts.previewSource || function () { return null; };
    var suspended = true;
    var fx = Object.assign(defaults(), JSON.parse(JSON.stringify(opts.state || {})));
    var canvas = root.querySelector('[data-role="preview"]');
    var ctx = canvas ? canvas.getContext("2d", { willReadFrequently: true }) : null;
    var baseFrame = null, renderPending = false;

    function render() {
      if (!ctx || !baseFrame || renderPending) return;
      renderPending = true;
      requestAnimationFrame(function () { renderPending = false; renderInto(ctx, canvas, baseFrame, fx); });
    }

    function drawPlaceholder(caption) {
      var g = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
      g.addColorStop(0, "#3b3663"); g.addColorStop(1, "#171426");
      ctx.fillStyle = g; ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#b9a5ff"; ctx.font = "11px sans-serif"; ctx.textAlign = "center";
      ctx.fillText("PLACEHOLDER — render a clip for a real preview frame", canvas.width / 2, canvas.height / 2);
      baseFrame = ctx.getImageData(0, 0, canvas.width, canvas.height);
      if (caption) caption.textContent = "Preview only — applied to each clip during post-processing";
      render();
    }

    function loadFrame() {
      if (!ctx) return;
      var src = previewSource();
      var caption = root.querySelector('[data-role="caption"]');
      if (!src) { drawPlaceholder(caption); return; }
      var img = new Image();
      img.onload = function () {
        var scale = Math.min(1, 640 / img.width);
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        baseFrame = ctx.getImageData(0, 0, canvas.width, canvas.height);
        if (caption) caption.textContent = "Preview only — applied during post-processing (" + (src.label || "clip frame") + ")";
        render();
      };
      img.onerror = function () { drawPlaceholder(caption); };
      img.src = src.url + (src.url.indexOf("?") >= 0 ? "&" : "?") + "t=" + Date.now();
    }

    function emit() { if (!suspended) onChange(JSON.parse(JSON.stringify(fx))); }

    function bind() {
      // master enable
      var en = root.querySelector('[data-field="enabled"]');
      if (en) en.addEventListener("change", function () { fx.enabled = en.checked; render(); emit(); });

      // sliders
      root.querySelectorAll(".cl-slider[data-path]").forEach(function (rowc) {
        var path = rowc.dataset.path;
        var input = rowc.querySelector("input");
        var out = rowc.querySelector("b");
        input.addEventListener("input", function () {
          var v = parseFloat(input.value);
          if (out) out.textContent = fmt(path, v);
          setPath(fx, path, v); render(); emit();
        });
      });

      // section toggles (vignette.enabled, film_grain.enabled, sharpness.enabled, vhs_effect.enabled, film_grain.animated)
      root.querySelectorAll('[data-field]').forEach(function (input) {
        var field = input.dataset.field;
        if (field === "enabled") return;
        input.addEventListener("change", function (e) {
          e.stopPropagation();
          setPath(fx, field, input.checked); render(); emit();
        });
      });
    }

    function hydrate(state) {
      suspended = true;
      fx = Object.assign(defaults(), JSON.parse(JSON.stringify(state || {})));
      var en = root.querySelector('[data-field="enabled"]');
      if (en) en.checked = !!fx.enabled;
      root.querySelectorAll(".cl-slider[data-path]").forEach(function (rowc) {
        var path = rowc.dataset.path;
        var input = rowc.querySelector("input");
        var out = rowc.querySelector("b");
        var v = getPath(fx, path);
        if (v == null) v = getPath(defaults(), path);
        input.value = v;
        if (out) out.textContent = fmt(path, v);
      });
      root.querySelectorAll('[data-field]').forEach(function (input) {
        var field = input.dataset.field;
        if (field === "enabled") return;
        input.checked = !!getPath(fx, field);
      });
      loadFrame();
      suspended = false;
    }

    bind();
    hydrate(opts.state || fx);
    return { hydrate: hydrate, getState: function () { return JSON.parse(JSON.stringify(fx)); }, root: root };
  }

  return { mount: mount, pipeline: pipeline, defaults: defaults, fmt: fmt };
})();
