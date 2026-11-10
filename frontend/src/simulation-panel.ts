/** Temporary single-load exploration using the server's actual projection engine. */
import {LitElement, css, html, svg, nothing, type PropertyValues, type TemplateResult} from "lit";
import {customElement,property,state} from "lit/decorators.js";
import {safeTimeZone} from "./forecast-data";
import {localInstants,type SimulationProjection,type SimulationResponse} from "./simulation";
import type {HomeAssistant} from "./types/hass";

@customElement("solar-sanity-simulation")
export class SolarSanitySimulation extends LitElement {
  @property({attribute:false}) public hass?:HomeAssistant;
  @property() public deviceId="";
  @property() public projectionId="";
  @property({type:Boolean}) public active=false;
  @state() protected _power="1000";
  @state() protected _minutes="60";
  @state() protected _mode="now";
  @state() protected _local="";
  @state() protected _fold="";
  @state() protected _started=false;
  @state() protected _loading=false;
  @state() protected _error="";
  @state() protected _result?:SimulationResponse;
  private _timer?:ReturnType<typeof setTimeout>;
  private _generation=0;
  private _running=false;
  private _queued=false;
  private _zone="";
  private readonly _visibility=()=>this._schedule();
  public override connectedCallback():void {super.connectedCallback();document.addEventListener("visibilitychange",this._visibility);this._schedule();}
  public override disconnectedCallback():void {super.disconnectedCallback();document.removeEventListener("visibilitychange",this._visibility);this._schedule();}
  protected updated(changed:PropertyValues):void {
    const zone=safeTimeZone(this.hass?.config.time_zone);
    if(this._zone && this._zone!==zone && this._mode==="scheduled") {this._local="";this._fold="";this._error="The time zone changed. Choose the start time again.";}
    this._zone=zone;
    if(changed.has("deviceId") && changed.get("deviceId")) {this._started=false;this._result=undefined;this._error="";}
    if(changed.has("projectionId")||changed.has("deviceId")||changed.has("active")) this._schedule();
  }
  private _candidates():string[] {return this._mode==="now"?[]:localInstants(this._local,safeTimeZone(this.hass?.config.time_zone));}
  private _validation():string|null {
    const power=Number(this._power), minutes=Number(this._minutes);
    if(this._power.trim()===""||!Number.isFinite(power)||power<0) return "Enter a finite non-negative power.";
    if(!Number.isInteger(minutes)||minutes<1||minutes>1440) return "Enter whole minutes from1 to1440.";
    if(this._mode==="scheduled") {
      if(!this._local) return "Choose a start date and time.";
      const candidates=this._candidates();
      if(!candidates.length) return "This local time does not exist. Choose another minute.";
      if(candidates.length>1 && this._fold==="") return "Choose which occurrence of the repeated time to use.";
    }
    return null;
  }
  private _schedule():void {
    this._generation++;if(this._timer)clearTimeout(this._timer);this._queued=false;
    if(!this._started||!this.active||!this.isConnected||document.hidden) {this._loading=false;return;}
    this._timer=setTimeout(()=>void this._calculate(),500);
  }
  private async _calculate():Promise<void> {
    if(!this._started||!this.active||!this.isConnected||document.hidden) return;
    const invalid=this._validation();if(invalid){this._error=invalid;this._loading=false;return;}
    if(!this.projectionId){this._error="Waiting for a valid current projection.";this._loading=false;return;}
    if(this._running){this._queued=true;return;}
    const generation=this._generation;
    this._running=true;this._loading=true;this._error="";
    const data:Record<string,unknown>={device_id:this.deviceId,power_w:Number(this._power),duration_minutes:Number(this._minutes)};
    if(this._mode==="scheduled")data.start_at=this._candidates()[this._fold===""?0:Number(this._fold)];
    try {
      const reply=await this.hass!.callWS<{response:SimulationResponse}>({type:"call_service",domain:"solar_sanity",service:"simulate_load",service_data:data,return_response:true});
      if(generation!==this._generation||!this.active||!this.isConnected)return;
      if(!reply.response?.basis || !Array.isArray(reply.response.scenario?.trajectory))throw new Error("The simulation response is unavailable.");
      if(reply.response.basis.projection_id!==this.projectionId){this._error="Waiting for the latest projection. The simulation will update with it.";return;}
      this._result=reply.response;
    } catch(error) {
      if(generation===this._generation && this.isConnected){
        this._error=typeof (error as {message?:unknown})?.message==="string"?(error as {message:string}).message:"Simulation could not be calculated. Check the current outlook and retry.";
        if(this._error.toLowerCase().includes("finished"))this._started=false;
      }
    } finally {
      this._running=false;if(generation===this._generation)this._loading=false;
      if(this._queued)this._schedule();
    }
  }
  private _input(key:"_power"|"_minutes"|"_local"|"_fold"|"_mode",event:Event):void {
    this[key]=(event.target as HTMLInputElement).value;if(key==="_local"||key==="_mode")this._fold="";this._schedule();
  }
  private _time(value:string|null):string {
    return value?new Intl.DateTimeFormat("en",{timeZone:safeTimeZone(this.hass?.config.time_zone),month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date(value)):"—";
  }
  private _graph(result:SimulationResponse):TemplateResult {
    const a=Date.parse(result.basis.computed_at),b=Date.parse(result.basis.horizon_end),span=b-a;
    const line=(p:SimulationProjection)=>p.trajectory.map(v=>`${((Date.parse(v.at)-a)/span*600).toFixed(2)},${(160-v.soc*1.5).toFixed(2)}`).join(" ");
    return html`<figure><svg viewBox="0 0 600 185" role="img" aria-label="Simulated battery charge: baseline solid line, additional load dashed line">
      <line x1="0" x2="600" y1=${160-result.basis.reserve_pct*1.5} y2=${160-result.basis.reserve_pct*1.5} class="reserve"/>
      ${svg`<polyline points=${line(result.baseline)} class="baseline"/><polyline points=${line(result.scenario)} class="scenario"/>`}
      <text x="0" y="180">${this._time(result.basis.computed_at)}</text><text x="600" y="180" text-anchor="end">${this._time(result.basis.horizon_end)}</text>
      </svg><figcaption>Solid: baseline · Dashed: extra load · Horizontal: reserve</figcaption></figure>`;
  }
  protected render():TemplateResult {
    const r=this._result,validation=this._validation(),candidates=this._candidates();
    return html`<section lang="en" dir="ltr"><p>Compare one extra load with the current projection. Calculated times can differ from the main card's settled times.</p>
      <div class="form"><label>Extra power (W)<input type="number" min="0" step="any" .value=${this._power} @input=${(e:Event)=>this._input("_power",e)}></label>
      <label>Duration (minutes)<input type="number" min="1" max="1440" step="1" .value=${this._minutes} @input=${(e:Event)=>this._input("_minutes",e)}></label>
      <label>Start<select .value=${this._mode} @change=${(e:Event)=>this._input("_mode",e)}><option value="now">Now</option><option value="scheduled">Scheduled</option></select></label>
      ${this._mode==="scheduled"?html`<label>Start date and time<input type="datetime-local" .value=${this._local} @input=${(e:Event)=>this._input("_local",e)}></label>
      ${candidates.length>1?html`<label>Repeated time<select .value=${this._fold} @change=${(e:Event)=>this._input("_fold",e)}><option value="">Choose occurrence</option>${candidates.map((v,i)=>html`<option value=${i}>${i===0?"First":"Second"}: ${v}</option>`)}</select></label>`:nothing}`:nothing}</div>
      <p class="muted">Time zone: ${safeTimeZone(this.hass?.config.time_zone)}</p>
      <button ?disabled=${Boolean(validation)||!this.projectionId} @click=${()=>{this._started=true;this._schedule();}}>Calculate</button>
      ${this._started?html`<span class="muted" role="status">${this._loading?"Updating…":"Updates automatically with the outlook"}</span>`:nothing}
      ${validation||this._error?html`<p role="alert">${validation??this._error}</p>`:nothing}
      ${!this.projectionId?html`<p>Waiting for a valid current projection.</p>`:nothing}
      ${r?html`<p>Based on ${this._time(r.basis.computed_at)} · ${r.basis.forecast_used?"Solar forecast":"Production history"}</p>${this._graph(r)}
        <table><thead><tr><th>Result</th><th>Baseline</th><th>Extra load</th></tr></thead><tbody>
        <tr><th>Night minimum</th><td>${r.baseline.lowest_pct===null?"—":`${r.baseline.lowest_pct.toFixed(1)}%`}</td><td>${r.scenario.lowest_pct===null?"—":`${r.scenario.lowest_pct.toFixed(1)}%`}</td></tr>
        <tr><th>Reserve at</th><td>${this._time(r.baseline.reserve_at)}</td><td>${this._time(r.scenario.reserve_at)}</td></tr>
        <tr><th>Full at</th><td>${this._time(r.baseline.full_at)}</td><td>${this._time(r.scenario.full_at)}</td></tr>
        <tr><th>Uncovered energy</th><td>${r.baseline.uncovered_energy_kwh.toFixed(2)} kWh</td><td>${r.scenario.uncovered_energy_kwh.toFixed(2)} kWh</td></tr></tbody></table>
        <p class="muted">Additional energy ${r.additional_energy_kwh.toFixed(2)} kWh · Applied ${this._time(r.applied_start)} to ${this._time(r.applied_end)}. Elapsed portions are excluded. A dash means no complete result within this horizon.</p>`:nothing}
      </section>`;
  }
  public static styles=css`
    :host{display:block} p{line-height:1.6} .muted,figcaption{color:var(--secondary-text-color);font-size:.875rem} .muted[role=status]{margin-inline-start:12px}
    .form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}label{display:grid;gap:6px;font-size:.875rem}
    input,select,button{box-sizing:border-box;width:100%;font:inherit;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:6px;padding:10px;min-width:0}
    button{width:auto;cursor:pointer}button:disabled{opacity:.6;cursor:default}button:hover{background:var(--secondary-background-color)}input:focus-visible,select:focus-visible,button:focus-visible{outline:2px solid var(--primary-color);outline-offset:3px}
    figure{margin:24px 0}svg{width:100%;display:block;overflow:visible}polyline{fill:none;stroke-width:2;vector-effect:non-scaling-stroke}.baseline{stroke:var(--primary-text-color)}.scenario{stroke:var(--primary-color);stroke-dasharray:7 4}.reserve{stroke:var(--divider-color);stroke-dasharray:3 3}text{fill:var(--secondary-text-color);font-size:11px}
    table{width:100%;border-collapse:collapse;font-size:.875rem;table-layout:fixed}td,th{text-align:left;padding:10px 4px;border-bottom:1px solid var(--divider-color);overflow-wrap:anywhere;font-variant-numeric:tabular-nums}th{font-weight:500}tbody th{color:var(--secondary-text-color)}
    @media(max-width:420px){.form{grid-template-columns:1fr}}
  `;
}
