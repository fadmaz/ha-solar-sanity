export interface PowerControl {
  manual_w: number | null;
  effective_w: number | null;
  source: "manual" | "learned" | "default";
}

export interface BatteryControls {
  battery_charge_power_limit_w: PowerControl;
  inverter_standby_w: PowerControl;
}

const watts = new Intl.NumberFormat("en", {maximumFractionDigits: 3});

export function powerControlLabel(control: PowerControl): string {
  if (control.effective_w === null) return "No limit learned · Auto";
  const source = {manual: "Manual", learned: "Learned", default: "Default"}[control.source];
  return `${watts.format(control.effective_w)} W · ${source}`;
}
