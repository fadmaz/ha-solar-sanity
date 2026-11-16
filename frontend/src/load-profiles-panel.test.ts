import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {SolarSanitySimulation} from "./simulation-panel";
import {type SavedLoad, type SolarSanityLoadProfiles} from "./load-profiles-panel";
import type {HomeAssistant} from "./types/hass";

const pump: SavedLoad = {id: "pump", name: "Heat pump", power_w: 820, duration_minutes: 45};
const loadList = (profiles = [pump], revision = 1, storage_error = false) =>
  ({response: {profiles, revision, storage_error}});

function make(callWS: ReturnType<typeof vi.fn>, admin = false) {
  const card = new SolarSanitySimulation();
  card.hass = {config: {state: "RUNNING", time_zone: "UTC"}, user: {is_admin: admin}, callWS} as unknown as HomeAssistant;
  card.deviceId = "first";
  card.active = true;
  card.planning = {mode: "battery", reserve_pct: 20, minimum_soc_pct: 50};
  document.body.append(card);
  return card;
}

function profiles(card: SolarSanitySimulation) {
  return card.shadowRoot!.querySelector<SolarSanityLoadProfiles>("solar-sanity-load-profiles")!;
}

async function settle(card: SolarSanitySimulation) {
  for (let index = 0; index < 4; index++) {
    await card.updateComplete;
    await profiles(card).updateComplete;
  }
}

function pick(card: SolarSanitySimulation, id: string) {
  field(profiles(card), "select", id, "change");
}

function field(scope: SolarSanitySimulation | SolarSanityLoadProfiles, selector: string, value: string, event = "input") {
  const element = scope.shadowRoot!.querySelector<HTMLInputElement>(selector)!;
  element.value = value;
  element.dispatchEvent(new Event(event));
}

function draft(card: SolarSanitySimulation) {
  return Array.from(card.shadowRoot!.querySelectorAll<HTMLInputElement>("input[type=number]")).map(input => input.value);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-16T10:00Z"));
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
});
afterEach(() => {
  document.body.innerHTML = "";
  vi.useRealTimers();
});

it("copies the saved numbers into both existing actions and keeps the planning window and limit", async () => {
  const call = vi.fn().mockImplementation(async ({service}) => service === "list_load_profiles" ? loadList() : {});
  const card = make(call);
  card.projectionId = "current";
  await settle(card);
  field(card, "[name=planning_limit]", "55");
  pick(card, pump.id);
  await settle(card);
  expect(draft(card)).toEqual(["820", "45", "55"]);
  expect(profiles(card).shadowRoot!.querySelector("select")!.value).toBe(pump.id);
  expect(profiles(card).shadowRoot!.textContent).not.toContain("Manage saved loads");
  await vi.advanceTimersByTimeAsync(600);
  expect(call.mock.calls.map(([args]) => args.service)).toEqual(["list_load_profiles"]);
  card.shadowRoot!.querySelector<HTMLButtonElement>("button")!.click();
  await vi.advanceTimersByTimeAsync(500);
  expect(call.mock.lastCall![0]).toMatchObject({service: "simulate_load", service_data: {
    device_id: "first", power_w: 820, duration_minutes: 45, minimum_soc_pct: 55,
  }});
  field(card, "select", "best_time", "change");
  await settle(card);
  field(card, "[name=finish_by]", "2026-11-16T15:00");
  field(card, "[name=earliest_start]", "2026-11-16T12:00");
  pick(card, "");
  pick(card, pump.id);
  await settle(card);
  card.shadowRoot!.querySelector<HTMLButtonElement>("button")!.click();
  await settle(card);
  expect(call.mock.lastCall![0]).toMatchObject({service: "find_best_time", service_data: {
    device_id: "first", power_w: 820, duration_minutes: 45, minimum_soc_pct: 55,
    finish_by: "2026-11-16T15:00:00.000Z", earliest_start: "2026-11-16T12:00:00.000Z",
  }});
});

it("maintains manual updates but discards a pending calculation after picking a different load", async () => {
  let reject!: (reason: Error) => void;
  const call = vi.fn().mockImplementation(({service}) => service === "list_load_profiles" ?
    Promise.resolve(loadList()) : new Promise((_resolve, rejectCall) => {reject = rejectCall;}));
  const card = make(call);
  card.projectionId = "current";
  await settle(card);
  pick(card, pump.id);
  await settle(card);
  card.shadowRoot!.querySelector<HTMLButtonElement>("button")!.click();
  await vi.advanceTimersByTimeAsync(500);
  reject(new Error("Try again"));
  await settle(card);
  field(card, "input[type=number]", "700");
  await vi.advanceTimersByTimeAsync(500);
  await settle(card);
  expect(profiles(card).shadowRoot!.querySelector("select")!.value).toBe("");
  expect(call.mock.lastCall![0].service_data.power_w).toBe(700);
  pick(card, pump.id);
  await settle(card);
  reject(new Error("Obsolete error"));
  await vi.advanceTimersByTimeAsync(750);
  await settle(card);
  expect(draft(card).slice(0, 2)).toEqual(["820", "45"]);
  expect(card.shadowRoot!.textContent).not.toContain("Obsolete error");
  expect(call.mock.calls.filter(([args]) => args.service === "simulate_load")).toHaveLength(2);
});

it("refreshes on opening What if, detaches edited loads and ignores the previous installation's reply", async () => {
  let resolve!: (response: unknown) => void;
  const call = vi.fn().mockResolvedValueOnce(loadList()).mockResolvedValueOnce(loadList([{...pump, power_w: 500}], 2))
    .mockImplementationOnce(() => new Promise(r => {resolve = r;})).mockResolvedValue(loadList([]));
  const card = make(call);
  card.planning = {mode: "solar_only", max_extra_grid_kwh: 0, revision: "synthetic"};
  await settle(card);
  pick(card, pump.id);
  await settle(card);
  card.active = false;
  await settle(card);
  card.active = true;
  await settle(card);
  expect(profiles(card).shadowRoot!.querySelector("select")!.value).toBe("");
  expect(draft(card)).toEqual(["820", "45", "0"]);
  card.active = false;
  await settle(card);
  card.active = true;
  await settle(card);
  card.deviceId = "other";
  await settle(card);
  resolve(loadList());
  await settle(card);
  expect(draft(card)).toEqual(["1000", "60", "0"]);
  expect(profiles(card).shadowRoot!.querySelector("select")!.options).toHaveLength(1);
});

it("prefills a new load and requires a separate delete confirmation without a projection", async () => {
  const call = vi.fn().mockImplementation(({service}) => Promise.resolve(
    service === "delete_load_profile" ? loadList([], 2) : service === "save_load_profile" ? loadList() : loadList([], 0),
  ));
  const card = make(call, true);
  await settle(card);
  field(card, "input[type=number]", "820");
  field(card, "input[max='1440']", "45");
  await settle(card);
  const root = profiles(card).shadowRoot!;
  root.querySelector<HTMLButtonElement>("#save-as-load")!.click();
  await settle(card);
  expect(root.activeElement?.getAttribute("name")).toBe("load_name");
  expect(root.querySelector<HTMLInputElement>("[name=load_power]")!.value).toBe("820");
  expect(root.querySelector<HTMLInputElement>("[name=load_minutes]")!.value).toBe("45");
  field(profiles(card), "[name=load_name]", "  Heat pump  ");
  root.querySelector("form")!.dispatchEvent(new Event("submit", {cancelable: true}));
  await settle(card);
  expect(call.mock.lastCall![0]).toMatchObject({service: "save_load_profile", service_data: {
    device_id: "first", name: "Heat pump", power_w: 820, duration_minutes: 45, revision: 0,
  }});
  pick(card, pump.id);
  await settle(card);
  root.querySelector<HTMLButtonElement>("[aria-label='Delete Heat pump']")!.click();
  await settle(card);
  expect(call.mock.calls.filter(([args]) => args.service === "delete_load_profile")).toHaveLength(0);
  expect(root.activeElement?.id).toBe("cancel-delete");
  root.querySelector<HTMLButtonElement>(".confirm button")!.click();
  await settle(card);
  expect(call.mock.lastCall![0]).toMatchObject({service: "delete_load_profile", service_data: {
    device_id: "first", profile_id: "pump", revision: 1,
  }});
  expect(root.querySelector("select")!.value).toBe("");
  expect(draft(card).slice(0, 2)).toEqual(["820", "45"]);
});

it("keeps manual controls usable after a failed list read and offers a retry", async () => {
  const call = vi.fn().mockRejectedValueOnce(new Error("Offline")).mockResolvedValue(loadList());
  const card = make(call, true);
  await settle(card);
  const root = profiles(card).shadowRoot!;
  expect(root.textContent).toContain("Saved loads are unavailable.");
  expect(root.querySelector("#save-as-load")).toBeNull();
  expect(card.shadowRoot!.querySelector<HTMLInputElement>("input[type=number]")!.disabled).toBe(false);
  root.querySelector<HTMLButtonElement>("button")!.click();
  await settle(card);
  expect(root.querySelector("select")!.options).toHaveLength(2);
});

it("restores all unsaved edits and their original revision after a list failure", async () => {
  const list = vi.fn().mockResolvedValueOnce(loadList([pump], 4))
    .mockRejectedValueOnce(new Error("Offline")).mockResolvedValue(loadList([pump], 4));
  const call = vi.fn().mockImplementation(async ({service}) => {
    if (service === "list_load_profiles") return list();
    if (service === "save_load_profile") {
      return loadList([{...pump, name: "Long cycle", power_w: 0, duration_minutes: 90}], 5);
    }
    return {};
  });
  const card = make(call, true);
  await settle(card);
  const panel = profiles(card);
  const root = panel.shadowRoot!;
  root.querySelector<HTMLButtonElement>("[aria-label='Edit Heat pump']")!.click();
  await settle(card);
  field(panel, "[name=load_name]", "  Long cycle  ");
  field(panel, "[name=load_power]", "0");
  field(panel, "[name=load_minutes]", "90");
  card.active = false;
  await settle(card);
  card.active = true;
  await settle(card);
  expect(root.querySelector("form")).toBeNull();
  root.querySelector<HTMLButtonElement>("button")!.click();
  await settle(card);
  expect(Array.from(root.querySelectorAll("form input"), input => (input as HTMLInputElement).value))
    .toEqual(["  Long cycle  ", "0", "90"]);
  root.querySelector("form")!.dispatchEvent(new Event("submit", {cancelable: true}));
  await settle(card);
  expect(call.mock.calls.find(([args]) => args.service === "save_load_profile")?.[0]).toMatchObject({
    service_data: {
    device_id: "first", profile_id: pump.id, revision: 4, name: "Long cycle", power_w: 0, duration_minutes: 90,
    },
  });
  expect(root.querySelector("form")).toBeNull();
});

it("keeps an edit visible after another administrator changes the list and clears it on installation switch", async () => {
  const list = vi.fn().mockResolvedValueOnce(loadList([pump], 1))
    .mockResolvedValueOnce(loadList([{...pump, power_w: 500}], 2))
    .mockResolvedValue(loadList([]));
  const call = vi.fn().mockImplementation(async ({service}) => service === "list_load_profiles" ? list() : {});
  const card = make(call, true);
  await settle(card);
  const panel = profiles(card);
  const root = panel.shadowRoot!;
  root.querySelector<HTMLButtonElement>("[aria-label='Edit Heat pump']")!.click();
  await settle(card);
  field(panel, "[name=load_power]", "720");
  card.active = false;
  await settle(card);
  card.active = true;
  await settle(card);
  expect(root.querySelector<HTMLInputElement>("[name=load_power]")!.value).toBe("720");
  expect(root.querySelector<HTMLButtonElement>("[type=submit]")!.disabled).toBe(true);
  expect(root.querySelector("[role=alert]")!.textContent).toContain("Cancel and reopen");
  root.querySelector("form")!.dispatchEvent(new Event("submit", {cancelable: true}));
  expect(call.mock.calls.some(([args]) => args.service === "save_load_profile")).toBe(false);
  card.deviceId = "other";
  await settle(card);
  expect(root.querySelector("form")).toBeNull();
  root.querySelector<HTMLButtonElement>("#save-as-load")!.click();
  await settle(card);
  expect(root.querySelector<HTMLInputElement>("[name=load_power]")!.value).toBe("1000");
});
