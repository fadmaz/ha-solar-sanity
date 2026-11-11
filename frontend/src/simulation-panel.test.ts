import {afterEach,beforeEach,describe,expect,it,vi} from "vitest";
import {SolarSanitySimulation} from "./simulation-panel";
import type {LoadPlanResponse, SimulationResponse} from "./simulation";
import type {HomeAssistant} from "./types/hass";

function response(id="one",energy=1):{response:SimulationResponse} {
  const projection={lowest_pct:40,full_at:null,reserve_at:null,uncovered_energy_kwh:0,
    trajectory:[{at:"2026-11-10T10:00:00Z",soc:80},{at:"2026-11-11T10:00:00Z",soc:40}]};
  return {response:{basis:{projection_id:id,computed_at:"2026-11-10T10:00:00Z",horizon_end:"2026-11-11T10:00:00Z",time_zone:"UTC",model_revision:"synthetic",forecast_used:true,reserve_pct:20,target_pct:100},
    applied_start:"2026-11-10T10:00:00Z",applied_end:"2026-11-10T11:00:00Z",additional_energy_kwh:energy,baseline:projection,scenario:projection}};
}

function planResponse(): {response: LoadPlanResponse} {
  const recommendation = {
    start_at: "2026-11-10T11:00:00Z", end_at: "2026-11-10T12:00:00Z",
    minimum_soc_pct: 45, additional_grid_kwh: 0, reserve_at: null,
  };
  return {response: {
    basis: response().response.basis, recommendation, reason_code: null,
    run_now: {...recommendation, start_at: "2026-11-10T10:00:00Z",
      end_at: "2026-11-10T11:00:00Z", minimum_soc_pct: 36},
  }};
}
async function settle(c:SolarSanitySimulation){await c.updateComplete;await Promise.resolve();await c.updateComplete;}
async function make(call=vi.fn().mockResolvedValue(response())){
  const c=new SolarSanitySimulation();c.hass={config:{time_zone:"UTC"},callWS:call} as unknown as HomeAssistant;
  c.deviceId="house";c.projectionId="one";c.active=true;document.body.append(c);await settle(c);return {c,call};
}
async function input(c:SolarSanitySimulation,selector:string,value:string,event="input"){
  const field=c.shadowRoot!.querySelector<HTMLInputElement>(selector)!;field.value=value;field.dispatchEvent(new Event(event));await settle(c);
}
async function start(c:SolarSanitySimulation){c.shadowRoot!.querySelector<HTMLButtonElement>("button")!.click();await settle(c);await vi.advanceTimersByTimeAsync(500);await settle(c);}
beforeEach(()=>{vi.useFakeTimers();vi.setSystemTime(new Date("2026-11-10T10:00:00Z"));vi.spyOn(document,"hidden","get").mockReturnValue(false);});
afterEach(()=>{document.body.innerHTML="";vi.useRealTimers();});

describe("live load exploration",()=>{
  it("waits for Calculate then debounces valid edits against the same native action",async()=>{
    const {c,call}=await make();expect(call).not.toHaveBeenCalled();await start(c);
    expect(call).toHaveBeenCalledWith(expect.objectContaining({service:"simulate_load",return_response:true,service_data:{device_id:"house",power_w:1000,duration_minutes:60}}));
    await input(c,"input[type=number]","2000");await input(c,"input[type=number]","3000");
    await vi.advanceTimersByTimeAsync(499);expect(call).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);expect(call).toHaveBeenCalledTimes(2);
    expect(call.mock.lastCall![0].service_data.power_w).toBe(3000);
  });
  it("allows only one request in flight and ignores its obsolete answer",async()=>{
    let resolve!:(v:unknown)=>void;
    const call=vi.fn().mockImplementationOnce(()=>new Promise(r=>{resolve=r;})).mockResolvedValue(response("two",2));
    const {c}=await make(call);await start(c);c.projectionId="two";await settle(c);await vi.advanceTimersByTimeAsync(500);
    expect(call).toHaveBeenCalledTimes(1);resolve(response("one",99));await settle(c);
    expect(c.shadowRoot!.textContent).not.toContain("99.00 kWh");
    await vi.advanceTimersByTimeAsync(500);await settle(c);expect(call).toHaveBeenCalledTimes(2);
    expect(c.shadowRoot!.textContent).toContain("Additional energy 2.00 kWh");
  });
  it("does not publish a previous installation's reply or carry its session",async()=>{
    let resolve!:(v:unknown)=>void;const call=vi.fn().mockImplementation(()=>new Promise(r=>{resolve=r;}));
    const {c}=await make(call);await start(c);c.deviceId="another";await settle(c);resolve(response());await settle(c);
    expect(c.shadowRoot!.querySelector("table")).toBeNull();await vi.advanceTimersByTimeAsync(1000);expect(call).toHaveBeenCalledTimes(1);
  });
  it("pauses hidden tabs and disconnected cards then resumes with the latest basis",async()=>{
    const {c,call}=await make();await start(c);c.active=false;c.projectionId="two";await settle(c);
    await vi.advanceTimersByTimeAsync(1000);expect(call).toHaveBeenCalledTimes(1);
    call.mockResolvedValue(response("two"));c.active=true;await settle(c);await vi.advanceTimersByTimeAsync(500);expect(call).toHaveBeenCalledTimes(2);
    c.remove();c.projectionId="three";await settle(c);await vi.advanceTimersByTimeAsync(1000);expect(call).toHaveBeenCalledTimes(2);
    call.mockResolvedValue(response("three"));document.body.append(c);await settle(c);await vi.advanceTimersByTimeAsync(500);expect(call).toHaveBeenCalledTimes(3);
  });
  it("stops work in a background document and resumes after visibility returns",async()=>{
    const {c,call}=await make();await start(c);
    vi.spyOn(document,"hidden","get").mockReturnValue(true);document.dispatchEvent(new Event("visibilitychange"));c.projectionId="two";await settle(c);
    await vi.advanceTimersByTimeAsync(1000);expect(call).toHaveBeenCalledTimes(1);
    vi.spyOn(document,"hidden","get").mockReturnValue(false);call.mockResolvedValue(response("two"));document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(500);expect(call).toHaveBeenCalledTimes(2);
  });
  it("removes stale results on invalid edits or source loss and recovers automatically",async()=>{
    const {c,call}=await make();await start(c);expect(c.shadowRoot!.querySelector("table")).not.toBeNull();
    await input(c,"input[type=number]","-1");await vi.advanceTimersByTimeAsync(500);await settle(c);expect(call).toHaveBeenCalledTimes(1);expect(c.shadowRoot!.querySelector("table")).toBeNull();
    await input(c,"input[type=number]","0");c.projectionId="";await settle(c);await vi.advanceTimersByTimeAsync(500);expect(call).toHaveBeenCalledTimes(1);
    c.projectionId="two";call.mockResolvedValue(response("two",0));await settle(c);await vi.advanceTimersByTimeAsync(500);await settle(c);expect(call).toHaveBeenCalledTimes(2);expect(c.shadowRoot!.textContent).toContain("Additional energy 0.00 kWh");
  });
  it("does not loop when the response precedes the entity's new projection token",async()=>{
    const {c,call}=await make(vi.fn().mockResolvedValue(response("two")));await start(c);
    await vi.advanceTimersByTimeAsync(5000);expect(call).toHaveBeenCalledTimes(1);expect(c.shadowRoot!.querySelector("table")).toBeNull();
    c.projectionId="two";await settle(c);await vi.advanceTimersByTimeAsync(500);await settle(c);expect(c.shadowRoot!.querySelector("table")).not.toBeNull();
  });
  it("uses the selected house minute and stops a scheduled scenario at its end",async()=>{
    const {c,call}=await make();await input(c,"select","scheduled","change");await input(c,"input[type=datetime-local]","2026-11-10T10:00");
    await input(c,"input[max='1440']","1");await start(c);
    expect(call.mock.lastCall![0].service_data.start_at).toBe("2026-11-10T10:00:00.000Z");
    await vi.advanceTimersByTimeAsync(60000);await settle(c);expect(c.shadowRoot!.textContent).toContain("This scenario has finished");
    c.projectionId="two";await settle(c);await vi.advanceTimersByTimeAsync(500);expect(call).toHaveBeenCalledTimes(1);
  });
  it("reports a distant start as outside the horizon instead of a finished run",async()=>{
    const {c,call}=await make(vi.fn().mockRejectedValue(new Error("The complete scenario must fit inside the projection horizon.")));
    await input(c,"select","scheduled","change");await input(c,"input[type=datetime-local]","2026-12-10T10:00");await start(c);
    await vi.advanceTimersByTimeAsync(2000);await settle(c);
    expect(call).toHaveBeenCalledTimes(1);expect(c.shadowRoot!.textContent).toContain("fit inside");
    expect(c.shadowRoot!.textContent).not.toContain("scenario has finished");
  });
});

describe("finding the best time", () => {
  it("makes one manual request, compares the options and marks an outdated plan", async () => {
    const {c, call} = await make(vi.fn().mockResolvedValue(planResponse()));
    await input(c, "select", "best_time", "change");
    expect(c.shadowRoot!.querySelector("button")!.disabled).toBe(true);
    await input(c, "input[name=finish_by]", "2026-11-10T13:00");
    expect(call).not.toHaveBeenCalled();
    await start(c);
    expect(call).toHaveBeenCalledWith(expect.objectContaining({
      service: "find_best_time", return_response: true,
      service_data: {device_id: "house", power_w: 1000, duration_minutes: 60,
        finish_by: "2026-11-10T13:00:00.000Z"},
    }));
    expect(c.shadowRoot!.querySelector("table")!.textContent).toContain("Run now");
    expect(c.shadowRoot!.textContent).toContain("45.0%");
    expect(c.shadowRoot!.textContent).toContain("36.0%");
    c.projectionId = "two";
    await settle(c);
    await vi.advanceTimersByTimeAsync(1000);
    expect(call).toHaveBeenCalledTimes(1);
    expect(c.shadowRoot!.textContent).toContain("Outlook changed");
  });

  it("ignores a pending result for a different installation", async () => {
    let resolve!: (v: unknown) => void;
    const call = vi.fn().mockImplementation(() => new Promise(r => {resolve = r;}));
    const {c} = await make(call);
    await input(c, "select", "best_time", "change");
    await input(c, "input[name=finish_by]", "2026-11-10T13:00");
    await start(c);
    c.deviceId = "another";
    await settle(c);
    resolve(planResponse());
    await settle(c);
    expect(c.shadowRoot!.querySelector("table")).toBeNull();
  });

  it("requires an occurrence for a repeated local deadline and explains no recommendation", async () => {
    vi.setSystemTime(new Date("2026-11-01T04:00:00Z"));
    const unavailable = planResponse();
    unavailable.response.recommendation = null;
    unavailable.response.run_now = null;
    unavailable.response.reason_code = "all_times_reach_reserve";
    const {c, call} = await make(vi.fn().mockResolvedValue(unavailable));
    c.hass = {...c.hass!, config: {...c.hass!.config, time_zone: "America/New_York"}};
    await settle(c);
    await input(c, "select", "best_time", "change");
    await input(c, "input[name=finish_by]", "2026-11-01T01:30");
    expect(c.shadowRoot!.querySelector("button")!.disabled).toBe(true);
    await input(c, "select[name=deadline_occurrence]", "1", "change");
    await start(c);
    expect(call.mock.lastCall![0].service_data.finish_by).toBe("2026-11-01T06:30:00.000Z");
    expect(c.shadowRoot!.textContent).toContain("at every tested time");
    expect(c.shadowRoot!.querySelector("table")).toBeNull();
  });
});
