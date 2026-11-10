/** Render server comparisons; elapsed energy and causes are never inferred here. */
import {html,type TemplateResult} from "lit";
import {safeTimeZone} from "./forecast-data";

interface ChangeField {key:string;previous:number;current:number;unit:string}
interface Changes {status:string;previous_at:string;current_at:string;common_start:string;common_end:string;fields:ChangeField[]}
const labels:Record<string,string>={soc_pct:"Battery charge",solar_energy:"Corrected solar",load_energy:"Expected consumption",scale:"Overall correction",scale_today:"Today's correction",scale_tomorrow:"Tomorrow's correction",max_charge_w:"Charging ceiling",standby_w:"Inverter standby"};

export function changesPanel(value:unknown,zone?:string):TemplateResult {
  const data=value as Partial<Changes>|undefined;
  if(data?.status!=="ok"||!Array.isArray(data.fields))return html`<p>${data?.status==="unavailable"?"A valid outlook is unavailable. Comparison will restart when it recovers.":"Waiting for two valid outlooks from the same setup. Comparisons restart after a restart, source or settings change."}</p>`;
  const time=(s:string|undefined)=>s&&Number.isFinite(Date.parse(s))?new Intl.DateTimeFormat("en",{timeZone:safeTimeZone(zone),month:"short",day:"numeric",hour:"2-digit",minute:"2-digit",hourCycle:"h23"}).format(new Date(s)):"—";
  const fields=data.fields.filter(f=>labels[f.key]&&Number.isFinite(f.previous)&&Number.isFinite(f.current));
  const number=(n:number,unit:string)=>`${new Intl.NumberFormat("en",{maximumFractionDigits:6}).format(n)}${unit?` ${unit}`:""}`;
  return html`<p>Input changes between ${time(data.previous_at)} and ${time(data.current_at)}.</p>
    <p class="muted">Solar and consumption use only the common future: ${time(data.common_start)} to ${time(data.common_end)}. These changes do not assign a share of the result to each input.</p>
    ${fields.length?html`<table class="changes"><thead><tr><th>Input</th><th>Previous</th><th>Current</th></tr></thead><tbody>${fields.map(f=>html`<tr><th>${labels[f.key]}</th><td>${number(f.previous,f.unit)}</td><td>${number(f.current,f.unit)}</td></tr>`)}</tbody></table><p class="muted">Unavailable estimates are omitted.</p>`:html`<p>Comparable inputs are unchanged. Unavailable estimates are omitted.</p>`}`;
}
