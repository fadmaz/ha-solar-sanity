import {afterEach, describe, expect, it, vi} from "vitest";
import {SolarSanityToolsCard, type HistoryResponse, type HistoryRow} from "./tools-card";
import type {SetupCheckResponse, SolarSanitySetupCheck} from "./setup-check";
import type {HomeAssistant} from "./types/hass";

function row(day="2026-11-10"): HistoryRow {
  return {id:`${day}:evening`, local_date:day, checkpoint:"evening", issued_at:`${day}T20:01:00Z`,
    time_zone:"UTC", model_revision:"synthetic", previous_setup:false,
    published:{lowest_pct:42,reserve_at:null,full_at:null},
    result:{status:"graded",actual_lowest:40,error:2,reserve_correct:true,time_error_minutes:null},reason:null} as HistoryRow;
}
const response = (records:HistoryRow[]=[]):HistoryResponse => ({time_zone:"UTC",status:records.length?"ok":"starting",reason_code:null,records});
function hass(callWS:unknown):HomeAssistant {
  return {config:{state:"RUNNING",time_zone:"UTC"},language:"ar",states:{},callWS,
    entities:{renamed:{entity_id:"sensor.renamed",platform:"solar_sanity",device_id:"one",translation_key:"battery_lowest_before_sunrise"}}} as unknown as HomeAssistant;
}
async function settle(c:SolarSanityToolsCard) {await c.updateComplete;await Promise.resolve();await c.updateComplete;}
async function make(h:HomeAssistant) {const c=new SolarSanityToolsCard();c.hass=h;document.body.append(c);await settle(c);return c;}
async function setup(c:SolarSanityToolsCard) {
  await settle(c);
  const panel = c.shadowRoot!.querySelector<SolarSanitySetupCheck>("solar-sanity-setup-check")!;
  await panel.updateComplete;
  await Promise.resolve();
  await panel.updateComplete;
  return panel.shadowRoot!;
}
function setupData(): SetupCheckResponse {
  const waiting = {status:"waiting_for_data",reason:null,action:null} as const;
  const off = {status:"not_configured",reason:"No battery outlook is set up.",action:"configure"} as const;
  return {time_zone:"Asia/Jerusalem",
    measurements:{...waiting,status:"needs_attention",channels:[
      {...waiting,key:"pv",kind:"energy",unit:"kWh",hours:11,status:"needs_attention",
        reason:"Energy sensors must use the total or total_increasing state class.",action:"reconfigure"},
      {...waiting,key:"load",kind:"power",unit:"W",hours:8,status:"ready"},
    ]},
    forecast:{...waiting,corrected:false,correction_reason:"Learning the roof.",providers:[
      {...waiting,integration:"forecast_solar",captured_at:"2026-11-11T06:30:00Z",
        verified_at:"2026-11-11T05:30:00Z",verification:"pending"},
    ]},
    battery:{...off,settings:null,charge_level:null},
    history:{...waiting,days:28,counts:null,exclusions:[]},
  };
}
afterEach(()=>{document.body.innerHTML="";});

describe("issued history tools",()=>{
  it("compares a complete solar hour in the saved time zone without replacing the night grade",async()=>{
    const r=row();
    r.time_zone="Asia/Jerusalem";r.until="2026-11-11T05:00:00Z";
    r.solar_coverage={status:"observed",observed_start:"2026-11-11T06:00:00Z",observed_end:"2026-11-11T07:00:00Z",
      relation:"later",reason:null,late_soc_status:"available",late_lowest_pct:38,late_soc_reason:null};
    const data=hass(vi.fn().mockResolvedValue({response:response([r])}));data.config.time_zone="America/Los_Angeles";
    const c=await make(data), section=c.shadowRoot!.querySelector(".solar-coverage")!;
    expect(section.textContent).toContain("Nov 11, 07:00");
    expect(section.textContent).toContain("08:00–09:00");
    expect(section.textContent).toContain("Later");
    expect(section.textContent).toContain("38%");
    expect(c.shadowRoot!.textContent).toContain("Minimum 40.0%");
  });
  it("keeps the observed hour when the later charge minimum is unavailable",async()=>{
    const r=row();r.until="2026-11-11T05:00:00Z";
    r.solar_coverage={status:"observed",observed_start:"2026-11-11T06:00:00Z",observed_end:"2026-11-11T07:00:00Z",
      relation:"later",reason:null,late_soc_status:"unavailable",late_lowest_pct:null,late_soc_reason:"Battery charge observations are incomplete."};
    const c=await make(hass(vi.fn().mockResolvedValue({response:response([r])})));
    expect(c.shadowRoot!.textContent).toContain("06:00–07:00");
    expect(c.shadowRoot!.textContent).toContain("Battery charge observations are incomplete.");
  });
  it("uses registry discovery and the native response action after an entity rename",async()=>{
    const call=vi.fn().mockResolvedValue({response:response([row()])}), c=await make(hass(call));
    expect(call).toHaveBeenCalledWith(expect.objectContaining({return_response:true,service:"get_forecast_history",service_data:{device_id:"one",days:28}}));
    expect(c.shadowRoot!.textContent).toContain("Minimum 42.0%");expect(c.shadowRoot!.textContent).toContain("Error 2.0 pp");
    expect(c.shadowRoot!.querySelector("select")!.value).toBe("28");
    expect(c.shadowRoot!.querySelector(".root")!.getAttribute("lang")).toBe("en");
  });
  it("separates empty history from read failure and allows retry",async()=>{
    const call=vi.fn().mockResolvedValue({response:response()}), c=await make(hass(call));
    expect(c.shadowRoot!.textContent).toContain("No issued predictions yet");
    call.mockRejectedValue(new Error("offline"));c.shadowRoot!.querySelector<HTMLButtonElement>(".toolbar button")!.click();await settle(c);
    expect(c.shadowRoot!.textContent).toContain("History could not be read");expect(c.shadowRoot!.textContent).not.toContain("No issued predictions yet");
  });
  it("never invents a minute timestamp from hourly fill evidence",async()=>{
    const r=row();r.checkpoint="morning";r.result.actual_filled=true;r.result.timing_resolution="hour";
    const c=await make(hass(vi.fn().mockResolvedValue({response:response([r])})));
    expect(c.shadowRoot!.textContent).toContain("Reached full · hourly evidence");expect(c.shadowRoot!.textContent).not.toContain("Timing error");
  });
  it("retains missed checkpoints and their exclusion reason",async()=>{
    const r=row();r.issued_at=null;r.result.status="missed";r.reason="Home Assistant was offline at the checkpoint.";
    const c=await make(hass(vi.fn().mockResolvedValue({response:response([r])})));
    expect(c.shadowRoot!.textContent).toContain("No prediction issued");expect(c.shadowRoot!.textContent).toContain("offline at the checkpoint");
  });
  it("paginates seven local dates and requests the selected period",async()=>{
    const call=vi.fn().mockResolvedValue({response:response(Array.from({length:9},(_,i)=>row(`2026-11-${String(10-i).padStart(2,"0")}`)))}),c=await make(hass(call));
    expect(c.shadowRoot!.querySelectorAll("article")).toHaveLength(7);
    [...c.shadowRoot!.querySelectorAll("button")].find(b=>b.textContent?.includes("earlier"))!.click();await settle(c);
    expect(c.shadowRoot!.querySelectorAll("article")).toHaveLength(2);
    [...c.shadowRoot!.querySelectorAll("button")].find(b=>b.textContent?.includes("Newer"))!.click();await settle(c);
    expect(c.shadowRoot!.querySelectorAll("article")).toHaveLength(7);
    const select=c.shadowRoot!.querySelector("select")!;select.value="90";select.dispatchEvent(new Event("change"));await settle(c);
    expect(call).toHaveBeenLastCalledWith(expect.objectContaining({service_data:{device_id:"one",days:90}}));
  });
  it("discards an old response after a newer request has completed",async()=>{
    let old:(v:unknown)=>void=()=>{};
    const call=vi.fn().mockImplementationOnce(()=>new Promise(resolve=>{old=resolve;})).mockResolvedValue({response:response([row("2026-11-09")])}),c=await make(hass(call));
    const select=c.shadowRoot!.querySelector("select")!;select.value="7";select.dispatchEvent(new Event("change"));await settle(c);
    old({response:response([row()])});await settle(c);
    expect(c.shadowRoot!.textContent).toContain("2026-11-09");expect(c.shadowRoot!.textContent).not.toContain("2026-11-10");
  });
  it("does not select a house silently when there are two",async()=>{
    const call=vi.fn(),data=hass(call);data.entities!.other={...data.entities!.renamed!,device_id:"two"};
    const c=await make(data);expect(call).not.toHaveBeenCalled();expect(c.shadowRoot!.textContent).toContain("Choose an installation");
  });
  it("distinguishes a selected period without rows from a journal without any history",async()=>{
    const r=response();r.status="ok";
    const c=await make(hass(vi.fn().mockResolvedValue({response:r})));
    expect(c.shadowRoot!.textContent).toContain("No predictions in the selected period");
    expect(c.shadowRoot!.textContent).not.toContain("No issued predictions yet");
  });
  it("reads input changes from the chosen sensor without requesting or inferring history",async()=>{
    const call=vi.fn(),data=hass(call);data.config.time_zone="Asia/Jerusalem";
    data.states["sensor.renamed"]={entity_id:"sensor.renamed",state:"40",last_updated:"",attributes:{outlook_changes:{status:"ok",previous_at:"2026-11-10T12:00:00Z",current_at:"2026-11-10T12:05:00Z",common_start:"2026-11-10T12:05:00Z",common_end:"2026-11-11T12:00:00Z",fields:[{key:"solar_energy",previous:8,current:9,unit:"kWh"}]}}};
    const c=new SolarSanityToolsCard();c.setConfig({type:"custom:solar-sanity-tools-card",default_tab:"changes"});c.hass=data;document.body.append(c);await settle(c);
    const panel=c.shadowRoot!.querySelector("#panel-changes")!;
    expect(panel.getAttribute("tabindex")).toBe("0");
    expect(panel.textContent).toContain("Nov 10, 14:05");expect(panel.textContent).toContain("8 kWh");expect(panel.textContent).toContain("9 kWh");expect(panel.textContent).toContain("common future");
    expect(call).not.toHaveBeenCalled();expect(c.shadowRoot!.querySelector("#tab-changes")!.getAttribute("aria-selected")).toBe("true");
    c.shadowRoot!.querySelector("#tab-changes")!.dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowRight"}));await settle(c);
    expect(c.shadowRoot!.querySelector("#tab-setup")!.getAttribute("aria-selected")).toBe("true");
    c.shadowRoot!.querySelector("#tab-setup")!.dispatchEvent(new KeyboardEvent("keydown",{key:"ArrowRight"}));await settle(c);
    expect(c.shadowRoot!.querySelector("#tab-history")!.getAttribute("aria-selected")).toBe("true");
  });
  it("distinguishes an unchanged pair, initial collection and a failed projection",async()=>{
    const c=new SolarSanityToolsCard();c.setConfig({type:"custom:solar-sanity-tools-card",default_tab:"changes"});c.hass=hass(vi.fn());document.body.append(c);await settle(c);
    const panel=()=>c.shadowRoot!.querySelector("#panel-changes")!;
    expect(panel().textContent).toContain("Waiting for two valid outlooks");
    const update=async(value:unknown)=>{c.hass={...c.hass!,states:{"sensor.renamed":{entity_id:"sensor.renamed",state:"40",last_updated:"",attributes:{outlook_changes:value}}}};await settle(c);};
    await update({status:"ok",fields:[]});expect(panel().textContent).toContain("Comparable inputs are unchanged");expect(panel().textContent).toContain("Unavailable estimates are omitted");
    await update({status:"unavailable"});expect(panel().textContent).toContain("valid outlook is unavailable");expect(panel().querySelector("table")).toBeNull();
  });
});

describe("setup check tools", () => {
  it("opens Setup directly for a solar-only installation and shows actionable checks in local time", async () => {
    const call = vi.fn().mockResolvedValue({response:setupData()}), data = hass(call);
    data.config.time_zone = "America/Los_Angeles";
    const collection = "sensor.collection";
    data.entities![collection] = {...data.entities!.renamed!, entity_id:collection, translation_key:"collection_status"};
    data.states[collection] = {entity_id:collection,state:"ok",last_updated:"",
      attributes:{battery_outlook_configured:false}};
    const c = await make(data), panel = await setup(c);
    expect([...c.shadowRoot!.querySelectorAll('[role="tab"]')].map(tab => tab.textContent)).toEqual(["Setup"]);
    expect(call).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      service:"get_setup_check",return_response:true,service_data:{device_id:"one"},
    }));
    expect(panel.textContent).toContain("11 retained hours");
    expect(panel.textContent).toContain("Reconfigure");
    expect(panel.textContent).toContain("Needs attention");
    expect(panel.textContent).toContain("No battery outlook is set up");
    expect(panel.textContent).toContain("Nov 11, 08:30");
    expect(panel.textContent).toContain("Nov 11, 07:30");
    expect(panel.textContent).toContain("Verification pending");
    expect(panel.querySelectorAll("section")).toHaveLength(4);
  });

  it("ignores a late setup response for the previous installation", async () => {
    let old: (v:unknown)=>void = ()=>{};
    const next = setupData();
    next.battery.settings = {capacity_kwh:13.2,reserve_pct:25,target_pct:90};
    const call = vi.fn().mockImplementationOnce(()=>new Promise(resolve=>{old=resolve;}))
      .mockResolvedValue({response:next});
    const data = hass(call);
    data.entities!.other = {...data.entities!.renamed!, device_id:"two"};
    const c = new SolarSanityToolsCard();
    c.setConfig({type:"custom:solar-sanity-tools-card",default_tab:"setup",device_id:"one"});
    c.hass = data; document.body.append(c);
    await setup(c);
    c.setConfig({type:"custom:solar-sanity-tools-card",default_tab:"setup",device_id:"two"});
    const panel = await setup(c);
    old({response:setupData()});
    await setup(c);
    expect(panel.textContent).toContain("13.2 kWh");
    expect(call.mock.calls.map(([request]) => request.service_data.device_id)).toEqual(["one","two"]);
  });

  it("recovers after a failed check without reading the hidden tab on unrelated updates", async () => {
    const call = vi.fn().mockRejectedValueOnce(new Error("unavailable")).mockResolvedValue({response:setupData()});
    const c = new SolarSanityToolsCard();
    c.setConfig({type:"custom:solar-sanity-tools-card",default_tab:"setup"});
    c.hass = hass(call); document.body.append(c);
    const panel = await setup(c);
    expect(panel.querySelector('[role="alert"]')?.textContent).toContain("Setup Check could not be read");
    panel.querySelector("button")!.click();
    await setup(c);
    expect(panel.textContent).toContain("Solar generation");
    c.shadowRoot!.querySelector<HTMLButtonElement>("#tab-changes")!.click();
    await settle(c);
    c.hass = {...c.hass!};
    await setup(c);
    expect(call).toHaveBeenCalledTimes(2);
  });
});
