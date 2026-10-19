// Zone colours (SP-662, SP-660): one ramp for every zone set — blue (easy) → cyan → green → khaki →
// yellow → orange → red → dark red (hard), the order of the reference tool's level heat map.
// A set with n zones samples the ramp evenly, so 5, 6 and 10 zones all run from blue to dark red.
//   ZoneColors.color(i, n)          → "#rrggbb" for zone i (0-based) of n
//   ZoneColors.tint(i, n, a)        → the same colour at alpha a (a row's background)
(function () {
  const RAMP = ["#3ba3dc", "#5fcfdc", "#26b37f", "#cbbd5a", "#f0bf1a", "#f08a24", "#e0473c", "#a3202f"];
  const rgb = (h) => [1, 3, 5].map((k) => parseInt(h.slice(k, k + 2), 16));
  const hex = (c) => "#" + c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");
  function mix(i, n) {
    const x = n <= 1 ? 0 : (i / (n - 1)) * (RAMP.length - 1);
    const k = Math.min(RAMP.length - 2, Math.floor(x)), f = x - k;
    const a = rgb(RAMP[k]), b = rgb(RAMP[k + 1]);
    return a.map((v, j) => v + (b[j] - v) * f);
  }
  const color = (i, n) => hex(mix(Math.max(0, Math.min(i, n - 1)), n));
  const tint = (i, n, a) => { const c = mix(Math.max(0, Math.min(i, n - 1)), n).map(Math.round); return `rgba(${c[0]},${c[1]},${c[2]},${a})`; };
  window.ZoneColors = { RAMP, color, tint };
})();
