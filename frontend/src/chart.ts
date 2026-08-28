/**
 * The smallest set of chart primitives that draws a day.
 *
 * Hand-rolled rather than a charting library, for reasons that are about this
 * product rather than about bundle size. CSS custom properties re-theme an SVG
 * with no JavaScript at all, where every canvas library needs a theme listener
 * and gets a custom theme subtly wrong — and a card whose pitch is "this looks
 * like Home Assistant" cannot afford that. The data is a few dozen points, so
 * canvas solves a problem nobody here has. And an SVG path is a string, which
 * means the drawing is testable.
 *
 * That last one is why coordinates are rounded on the way out. A `d` attribute
 * full of seventeen-digit floats is a snapshot nobody can read and one that
 * shifts under the smallest change; two decimals is a hundredth of a pixel,
 * which no display can show and no reviewer has to squint at.
 */

/** Maps a value in the data's units to a position along an axis. */
export type Scale = (value: number) => number;

/** A point in chart space, already scaled. */
export type Point = readonly [x: number, y: number];

const PRECISION = 2;

/**
 * Round for output.
 *
 * Negative zero is folded into zero: `-0` and `0` are equal in JavaScript but
 * render as different strings, so without this a path could differ from an
 * identical one purely in the sign of a coordinate that is not there.
 */
export function round(value: number): number {
  if (!Number.isFinite(value)) return 0;
  const factor = 10 ** PRECISION;
  const rounded = Math.round(value * factor) / factor;
  return Object.is(rounded, -0) ? 0 : rounded;
}

/**
 * A linear scale from a data range to a pixel range.
 *
 * A zero-width domain maps everything to the middle of the range rather than
 * dividing by zero. That is not a rare case — it is a day before dawn, when
 * every value so far is zero, and it must draw a flat line rather than a hole.
 */
export function scaleLinear(
  domain: readonly [number, number],
  range: readonly [number, number],
): Scale {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0;
  if (span === 0) return () => (r0 + r1) / 2;
  return (value: number) => r0 + ((value - d0) / span) * (r1 - r0);
}

/**
 * A polyline through the points, as a `d` attribute.
 *
 * Empty for fewer than two points. One point is not a line, and drawing a
 * zero-length segment leaves a dot on the chart that reads as data.
 */
export function linePath(points: readonly Point[]): string {
  if (points.length < 2) return "";
  return points
    .map(([x, y], index) => `${index === 0 ? "M" : "L"}${round(x)},${round(y)}`)
    .join(" ");
}

/**
 * The same shape closed down to a baseline, for a filled area.
 *
 * Kept separate from `linePath` rather than parameterised: the stroke and the
 * fill want different opacity and different `paint-order`, so they are two
 * elements regardless, and two functions is honest about that.
 */
export function areaPath(points: readonly Point[], baseline: number): string {
  if (points.length < 2) return "";
  const first = points[0]!;
  const last = points[points.length - 1]!;
  return [
    linePath(points),
    `L${round(last[0])},${round(baseline)}`,
    `L${round(first[0])},${round(baseline)}`,
    "Z",
  ].join(" ");
}

/**
 * Round numbers covering `max`, for an axis that reads well.
 *
 * Deliberately crude — 1, 2 or 5 times a power of ten. A general-purpose tick
 * algorithm is a lot of code to make an axis marginally prettier, and this one
 * fits in a paragraph.
 */
export function niceTicks(max: number, count = 4): number[] {
  if (!Number.isFinite(max) || max <= 0 || count < 1) return [0];
  const rough = max / count;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * magnitude).find((s) => s >= rough) ?? magnitude * 10;

  // Up to the first multiple of the step at or above the maximum, so the axis
  // always contains the data. Stopping at the last step below it left the top
  // of the curve outside the chart, which is the one part of a solar day
  // anybody looks at.
  const top = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let value = 0; value <= top + step / 2; value += step) ticks.push(round(value));
  return ticks;
}

/**
 * Hours of a local day as `HH` labels, for the axis.
 *
 * Takes the dates rather than assuming a day starts at midnight or has
 * twenty-four hours, because two days a year it does neither.
 */
export function hourLabels(hours: readonly Date[], every = 6): string[] {
  return hours.map((hour, index) =>
    index % every === 0 ? String(hour.getHours()).padStart(2, "0") : "",
  );
}
