import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { BatteryControls } from "./battery-controls";
import { SolarSanityBatterySettingsPreview, type PreviewResponse } from "./battery-settings-preview";
import type { HomeAssistant } from "./types/hass";

const controls: BatteryControls = {
  battery_charge_power_limit_w: {manual_w: null, effective_w: 2500, source: "learned"},
  inverter_standby_w: {manual_w: 0, effective_w: 0, source: "manual"},
};

function response(id = "one", lowest = 42): {response: PreviewResponse} {
  const result = {lowest_pct: lowest, reserve_at: null, full_at: null,
    takeover_at: "2026-11-11T05:30:00Z", controls};
  return {response: {basis:{projection_id:id, computed_at:"2026-11-10T19:00:00Z",
    time_zone:"Asia/Jerusalem"}, current:result, proposed:result}};
}

async function make(call = vi.fn().mockResolvedValue(response())) {
  const card = new SolarSanityBatterySettingsPreview();
  card.hass = {callWS:call, config:{time_zone:"America/Los_Angeles"}} as unknown as HomeAssistant;
  card.active = true;
  card.deviceId = "synthetic-device";
  card.projectionId = "one";
  card.controls = controls;
  document.body.append(card);
  await card.updateComplete;
  await card.updateComplete;
  const root = card.shadowRoot!;
  const details = root.querySelector("details")!;
  details.open = true;
  details.dispatchEvent(new Event("toggle"));
  return {card, root, call};
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
});
afterEach(() => {
  document.body.innerHTML = "";
  vi.useRealTimers();
  vi.restoreAllMocks();
});

it("prefills Manual zero and sends blank as Auto from the installed What if control", async () => {
  const {card, root, call} = await make();
  expect(call).not.toHaveBeenCalled();
  expect([...root.querySelectorAll("input")].map(input => input.value)).toEqual(["", "0"]);
  root.querySelector("button")!.click();
  await vi.advanceTimersByTimeAsync(500);
  await card.updateComplete;
  expect(call).toHaveBeenCalledWith(expect.objectContaining({
    service:"preview_battery_settings", return_response:true,
    service_data:{device_id:"synthetic-device",projection_id:"one",
      battery_charge_power_limit_w:null,inverter_standby_w:0},
  }));
  expect(root.querySelector("table")?.textContent).toContain("Nov 11, 07:30");
  expect(root.querySelector("table")?.textContent).toContain("0 W · Manual");
  const limit = root.querySelector("input")!;
  limit.value = "0";
  limit.dispatchEvent(new Event("input"));
  await vi.advanceTimersByTimeAsync(1000);
  expect(call).toHaveBeenCalledTimes(1);
  expect(root.querySelector("table")).toBeNull();
  expect(root.querySelector('[role="alert"]')?.textContent).toContain("above zero");
});

it("discards a pending response after the device changes and previews the new device's values", async () => {
  let finish!: (data: unknown) => void;
  const call = vi.fn().mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}))
    .mockResolvedValue(response("two", 55));
  const {card, root} = await make(call);
  root.querySelector("button")!.click();
  await vi.advanceTimersByTimeAsync(500);
  card.deviceId = "another-device";
  card.projectionId = "two";
  card.controls = {...controls, battery_charge_power_limit_w:{manual_w:1800,effective_w:1800,source:"manual"}};
  await card.updateComplete;
  await vi.advanceTimersByTimeAsync(500);
  expect(call).toHaveBeenCalledTimes(1);
  finish(response("one", 98));
  await vi.advanceTimersByTimeAsync(500);
  await card.updateComplete;
  expect(root.querySelector("table")?.textContent).toContain("55.0%");
  expect(root.querySelector("table")?.textContent).not.toContain("98.0%");
  expect(call.mock.lastCall?.[0].service_data).toMatchObject({
    device_id:"another-device",projection_id:"two",battery_charge_power_limit_w:1800,
  });
  card.active = false;
  card.projectionId = "three";
  await card.updateComplete;
  await vi.advanceTimersByTimeAsync(1000);
  expect(call).toHaveBeenCalledTimes(2);
});
