import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {SolarSanityLoadReminder} from "./load-reminder-panel";
import type {HomeAssistant} from "./types/hass";

const empty = {response: {reminder: null, storage_error: false}};
const scheduled = {
  id: "reminder", status: "scheduled", destination: "notify.phone",
  power_w: 1000, duration_minutes: 60, reason_code: null,
  scheduled_at: "2026-11-10T11:00:00Z", latest_start: "2026-11-10T12:00:00Z",
};

async function settle(card: SolarSanityLoadReminder) {
  await card.updateComplete;
  await Promise.resolve();
  await card.updateComplete;
}

async function mount(call = vi.fn().mockResolvedValue(empty)) {
  const card = new SolarSanityLoadReminder();
  card.hass = {
    config: {time_zone: "Asia/Jerusalem"},
    states: {"notify.phone": {state: "unknown", attributes: {friendly_name: "Phone"}}},
    services: {notify: {mobile_app_tablet: {}}},
    callWS: call,
  } as unknown as HomeAssistant;
  card.deviceId = "house";
  card.projectionId = "current";
  card.active = true;
  card.request = {device_id: "house", power_w: 1000, duration_minutes: 60, finish_by: "2026-11-10T13:00:00Z"};
  document.body.append(card);
  await settle(card);
  return {card, call};
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

it.each(["notify.phone", "mobile_app_tablet"])("requires an explicit destination and sends the current plan to %s", async destination => {
  let current = empty.response as {reminder: unknown; storage_error: boolean};
  const call = vi.fn().mockImplementation(async ({service}) => {
    if (service === "schedule_load_reminder") current = {reminder: scheduled, storage_error: false};
    if (service === "cancel_load_reminder") current = {reminder: {...scheduled, status: "cancelled"}, storage_error: false};
    return {response: current};
  });
  const {card} = await mount(call);
  const root = card.shadowRoot!;
  expect(root.querySelector("button")!.disabled).toBe(true);
  const select = root.querySelector("select")!;
  select.value = destination;
  select.dispatchEvent(new Event("change"));
  await settle(card);
  root.querySelector("button")!.click();
  await settle(card);
  expect(call).toHaveBeenCalledWith(expect.objectContaining({
    service: "schedule_load_reminder",
    service_data: {...card.request, destination, projection_id: "current"},
  }));
  expect(root.textContent).toContain("13:00");
  expect(root.textContent).toContain("14:00");
  expect(root.querySelector("button")!.textContent).toContain("Cancel reminder");
  root.querySelector("button")!.click();
  await settle(card);
  expect(call).toHaveBeenCalledWith(expect.objectContaining({
    service: "cancel_load_reminder", service_data: {device_id: "house", reminder_id: "reminder"},
  }));
  expect(root.textContent).toContain("Reminder cancelled");
});

it("polls only while What if is visible and ignores replies from another installation", async () => {
  let resolveOld!: (reply: unknown) => void;
  const call = vi.fn().mockImplementation(({service_data}) => service_data.device_id === "house"
    ? new Promise(resolve => {resolveOld = resolve;})
    : Promise.resolve(empty));
  const {card} = await mount(call);
  card.deviceId = "other";
  card.request = undefined;
  await settle(card);
  resolveOld({response: {reminder: scheduled, storage_error: false}});
  await settle(card);
  expect(card.shadowRoot!.textContent).not.toContain("Reminder scheduled");
  await vi.advanceTimersByTimeAsync(30_000);
  const visibleCalls = call.mock.calls.length;
  card.active = false;
  await settle(card);
  await vi.advanceTimersByTimeAsync(60_000);
  expect(call).toHaveBeenCalledTimes(visibleCalls);
  card.active = true;
  await settle(card);
  expect(call).toHaveBeenCalledTimes(visibleCalls + 1);
});
