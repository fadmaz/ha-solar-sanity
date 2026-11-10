import {describe,expect,it} from "vitest";
import {localInstants} from "./simulation";

describe("scenario wall-clock input",()=>{
  it("uses the house time zone independently of the browser",()=>{
    expect(localInstants("2026-11-10T18:00","Asia/Jerusalem")).toEqual(["2026-11-10T16:00:00.000Z"]);
  });
  it("rejects a missing spring-clock minute",()=>{
    expect(localInstants("2026-03-08T02:30","America/New_York")).toEqual([]);
  });
  it("returns both occurrences rather than choosing a repeated hour silently",()=>{
    expect(localInstants("2026-11-01T01:30","America/New_York")).toEqual(["2026-11-01T05:30:00.000Z","2026-11-01T06:30:00.000Z"]);
  });
  it("rejects invalid dates",()=>{expect(localInstants("2026-02-30T12:00","UTC")).toEqual([]);});
});
