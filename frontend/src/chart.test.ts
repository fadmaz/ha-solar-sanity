import { describe, expect, it } from "vitest";

import { areaPath, hourLabels, linePath, niceTicks, round, scaleLinear } from "./chart";

describe("round", () => {
  it("keeps a hundredth of a pixel and no more", () => {
    expect(round(12.3456789)).toBe(12.35);
  });

  it("folds negative zero into zero", () => {
    // They are equal in JavaScript and render as different strings, so without
    // this two identical paths could differ in the sign of a coordinate that is
    // not there.
    expect(Object.is(round(-0.001), 0)).toBe(true);
  });

  it("refuses to emit a coordinate that is not a number", () => {
    expect(round(Number.NaN)).toBe(0);
    expect(round(Number.POSITIVE_INFINITY)).toBe(0);
  });
});

describe("scaleLinear", () => {
  it("maps the ends of the domain to the ends of the range", () => {
    const y = scaleLinear([0, 10], [100, 0]);

    expect(y(0)).toBe(100);
    expect(y(10)).toBe(0);
  });

  it("is linear in between", () => {
    const y = scaleLinear([0, 10], [100, 0]);

    expect(y(2.5)).toBe(75);
  });

  it("does not divide by zero on a flat domain", () => {
    // A day before dawn: every value so far is zero. It must draw a flat line,
    // not a hole.
    const y = scaleLinear([0, 0], [100, 0]);

    expect(y(0)).toBe(50);
    expect(Number.isFinite(y(5))).toBe(true);
  });

  it("extrapolates rather than clamping", () => {
    // Clamping silently would hide a value that is off the chart, which on a
    // chart about trustworthy numbers is the wrong instinct.
    const y = scaleLinear([0, 10], [0, 100]);

    expect(y(20)).toBe(200);
  });
});

describe("linePath", () => {
  it("is the drawing, so it is the test", () => {
    const d = linePath([
      [0, 100],
      [10, 50],
      [20, 0],
    ]);

    expect(d).toBe("M0,100 L10,50 L20,0");
  });

  it("rounds every coordinate", () => {
    const d = linePath([
      [0.123456, 99.987654],
      [10.5, 0.005],
    ]);

    expect(d).toBe("M0.12,99.99 L10.5,0.01");
  });

  it("draws nothing for a single point", () => {
    // A zero-length segment leaves a dot on the chart that reads as data.
    expect(linePath([[5, 5]])).toBe("");
  });

  it("draws nothing for no points", () => {
    expect(linePath([])).toBe("");
  });
});

describe("areaPath", () => {
  it("closes the shape down to the baseline", () => {
    const d = areaPath(
      [
        [0, 50],
        [10, 20],
      ],
      100,
    );

    expect(d).toBe("M0,50 L10,20 L10,100 L0,100 Z");
  });

  it("draws nothing when the line would not be drawn", () => {
    expect(areaPath([[1, 2]], 100)).toBe("");
  });
});

describe("niceTicks", () => {
  it("covers the maximum", () => {
    const ticks = niceTicks(37);

    expect(ticks.at(-1)!).toBeGreaterThanOrEqual(37);
  });

  it("starts at zero, because energy does", () => {
    expect(niceTicks(37)[0]).toBe(0);
  });

  it("uses round numbers", () => {
    expect(niceTicks(37)).toEqual([0, 10, 20, 30, 40]);
    expect(niceTicks(4.2)).toEqual([0, 2, 4, 6]);
  });

  it("goes past the maximum rather than stopping below it", () => {
    // The regression: 4.2 used to stop at 4, leaving the top of the curve
    // outside the chart — which is the one part of a solar day anybody looks at.
    for (const max of [4.2, 9.9, 37, 0.3, 1234]) {
      expect(niceTicks(max).at(-1)!).toBeGreaterThanOrEqual(max);
    }
  });

  it("survives a day with no generation at all", () => {
    expect(niceTicks(0)).toEqual([0]);
    expect(niceTicks(Number.NaN)).toEqual([0]);
  });

  it("never returns an unbounded list", () => {
    expect(niceTicks(1e9).length).toBeLessThan(20);
  });
});

describe("hourLabels", () => {
  const day = (hours: number[]) => hours.map((h) => new Date(2026, 7, 28, h));

  it("labels every sixth hour and blanks the rest", () => {
    const labels = hourLabels(day([0, 1, 2, 3, 4, 5, 6]));

    expect(labels[0]).toBe("00");
    expect(labels[6]).toBe("06");
    expect(labels.slice(1, 6)).toEqual(["", "", "", "", ""]);
  });

  it("pads to two digits so the axis does not jitter", () => {
    expect(hourLabels(day([9]), 1)).toEqual(["09"]);
  });

  it("reads the hour off the date rather than the index", () => {
    // Two days a year a local day does not start at midnight or hold
    // twenty-four hours, and an index would be wrong on both.
    expect(hourLabels(day([13, 14]), 1)).toEqual(["13", "14"]);
  });
});
