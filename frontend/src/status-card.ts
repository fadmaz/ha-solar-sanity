/**
 * The status card — the wedge, rendered.
 *
 * Design rules, and they are the feature rather than the styling:
 *
 *  - **Silence is the default.** A healthy day says so in one line and shows
 *    nothing else. No chart, no gauge, no "last updated 3 seconds ago". A card
 *    that performs activity to look useful gets deleted within a week.
 *  - **Always exactly three rows.** It never grows on alert. A card that
 *    reflows the whole dashboard when something goes wrong is worse than no
 *    card.
 *  - **Never colour alone.** Every state carries a glyph, a headline word and a
 *    colour, in that order of primacy. A greyscale screenshot must stay fully
 *    readable.
 *  - **Every degraded state is a helpful sentence**, never a stack trace and
 *    never an empty box.
 */

import { LitElement, css, html, nothing, type TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import type {
  GridOptions,
  HassEntity,
  HomeAssistant,
  LovelaceCardConfig,
  SolarSanityStatus,
  SolarSanityStatusAttributes,
} from "./types/hass";

const ENTITY_PREFIX = "sensor.";

/**
 * The five verdicts, which together identify our status entity.
 *
 * Matching on the entity id does not work, in either direction. Requiring
 * `solar_sanity` in it breaks the moment somebody renames the entity — and
 * renaming is ordinary. Dropping that and matching `_status` instead picks up
 * every other integration in the house: a camera, an alarm panel, a router.
 *
 * An enum sensor publishes its `options`, and no other integration publishes
 * this particular list. It survives a rename, cannot collide, and needs no
 * lookup — which is the whole reason this card takes no `entity:` option.
 */
const STATUSES = [
  "ok",
  "insufficient_data",
  "not_checkable",
  "investigating",
  "fault_found",
] as const;

/** Whether a state object is one of ours. */
export function isStatusEntity(entity: HassEntity | undefined): boolean {
  const options = entity?.attributes?.options;
  if (!Array.isArray(options)) return false;
  return STATUSES.every((status) => options.includes(status));
}

interface StatusCardConfig extends LovelaceCardConfig {
  entity?: string;
  name?: string;
  show_evidence?: boolean;
}

/**
 * The most the card may say before it stops being a glance.
 *
 * A fault's full explanation runs to 281 characters, and this card is pinned to
 * three rows on purpose: one that reflows the whole dashboard the moment
 * something goes wrong is worse than no card. But truncating an explanation
 * mid-thought is its own small dishonesty, and the reader is left holding half
 * a sentence about their own house.
 *
 * So the card takes the *lead* — the observation, which is the part that is
 * about them — and the rest stays where it was always going to be read
 * properly: the Repairs entry the "Show me" button opens, which carries the
 * whole thing plus how to fix it.
 */
const LEAD_MAX_CHARS = 130;

/** The first sentence, or a clean truncation if that sentence is itself long. */
export function leadSentence(text: string, maxChars = LEAD_MAX_CHARS): string {
  const trimmed = text.trim();
  if (!trimmed) return "";

  // A period followed by a space and a capital. Not a bare period: the copy is
  // full of figures like "1234.5" and "8.83%", and splitting those mid-number
  // would be worse than not splitting at all.
  const boundary = trimmed.search(/\.\s+[A-Z]/);
  const first = boundary === -1 ? trimmed : trimmed.slice(0, boundary + 1);
  if (first.length <= maxChars) return first;

  const cut = first.lastIndexOf(" ", maxChars);
  return `${first.slice(0, cut === -1 ? maxChars : cut)}…`;
}

type Glyph = "ok" | "alert" | "waiting" | "unknown";

interface Verdict {
  glyph: Glyph;
  headline: string;
  body: string;
  /** The unabridged text, when `body` is a lead rather than the whole thing. */
  full?: string;
  action?: { label: string; href: string };
  /**
   * What the engine wants said beside the verdict.
   *
   * Not folded into `body`. A note qualifies the answer rather than replacing
   * it — "only the night hours could be checked", "your generation sensor reads
   * 4% above the rest of the system" — and a reader needs to see which is the
   * verdict and which is the caveat.
   */
  notes?: string[];
}

/**
 * Copy selection, as a pure function.
 *
 * Kept separate and side-effect free so it can be table-tested. For a product
 * whose promise is tone, the wording is an artifact worth protecting from a
 * well-meaning refactor.
 */
export function verdictFor(
  // Not `SolarSanityStatus`. An entity's state is also `unavailable` or
  // `unknown`, and typing it as only the five the engine emits was a claim the
  // runtime does not honour — which is how those two ended up sharing a branch
  // with "not installed" and telling the user to install what they already had.
  status: SolarSanityStatus | "unavailable" | "unknown" | undefined,
  attrs: SolarSanityStatusAttributes,
): Verdict {
  // Carried onto every verdict below rather than onto some of them. A note is
  // a qualification of whatever was decided, and the verdict it qualifies most
  // is `ok` — that is where "everything reconciles" would otherwise be the
  // whole message on a house that reconciles only over a day, or only in the
  // hours with no generation.
  const notes = attrs.notes?.filter((note) => note.trim().length > 0);

  switch (status) {
    case "ok":
      return {
        glyph: "ok",
        headline: "Data checks out",
        body: attrs.days_of_data
          ? `Everything reconciles across ${attrs.days_of_data} days of data.`
          : "Everything reconciles.",
        notes,
      };

    case "fault_found":
      return {
        glyph: "alert",
        headline: attrs.headline ?? "Something does not add up",
        body: leadSentence(attrs.detail ?? ""),
        // The rest of the explanation, and what to do about it, is in the
        // Repairs entry this opens. It is never only on the card.
        full: attrs.detail ?? undefined,
        action: { label: "Show me", href: "/config/repairs" },
        notes,
      };

    case "investigating":
      return {
        glyph: "waiting",
        headline: "Still looking",
        body:
          attrs.reason ??
          "The numbers move around, but not in a way I can name yet. Patterns usually declare themselves given another week.",
        notes,
      };

    case "insufficient_data":
      return {
        glyph: "waiting",
        headline: "Not enough data yet",
        body: attrs.days_of_data
          ? `${attrs.days_of_data} days so far. The first verdict needs about a week.`
          : "Come back after a few full days — the first verdict needs about a week.",
      };

    case "not_checkable":
      return {
        glyph: "unknown",
        headline: "Cannot be checked",
        body:
          attrs.reason ??
          "Something needed for the arithmetic is missing, so any verdict would be meaningless.",
        action: { label: "Configure", href: "/config/integrations" },
      };

    case "unavailable":
    case "unknown":
      // Present but not answering. Telling this user to install the thing they
      // already have is worse than saying nothing: it sends them to add a
      // second copy, which is its own well-documented mess.
      return {
        glyph: "unknown",
        headline: "Not answering right now",
        body: "Solar Sanity is installed but its status is unavailable. This usually clears by itself after a restart.",
      };

    default:
      return {
        glyph: "unknown",
        headline: "Solar Sanity is not set up yet",
        body: "Add the integration and this card fills in on its own.",
        action: {
          label: "Set it up",
          href: "/config/integrations/dashboard/add?domain=solar_sanity",
        },
      };
  }
}

const GLYPHS: Record<Glyph, string> = {
  // Simple inline paths rather than <ha-svg-icon>, so the card body depends on
  // no Home Assistant elements at all.
  ok: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20m-1 15-4-4 1.4-1.4L11 14.2l5.6-5.6L18 10z",
  alert: "M13 14h-2V9h2m0 9h-2v-2h2M1 21h22L12 2z",
  waiting: "M12 20a8 8 0 1 1 0-16 8 8 0 0 1 0 16m0-18a10 10 0 1 0 0 20 10 10 0 0 0 0-20m.5 5H11v6l5.2 3.2.8-1.3-4.5-2.7z",
  unknown:
    "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20m1 17h-2v-2h2m1.1-7.7-.9.9c-.7.7-1.2 1.3-1.2 2.8h-2v-.5c0-1.1.4-2.1 1.2-2.8l1.2-1.3c.4-.3.6-.8.6-1.4a2 2 0 0 0-4 0H8a4 4 0 0 1 8 0c0 .9-.4 1.7-.9 2.3",
};

@customElement("solar-sanity-card")
export class SolarSanityCard extends LitElement {
  @property({ attribute: false }) public hass?: HomeAssistant;
  @state() private _config?: StatusCardConfig;

  public setConfig(config: StatusCardConfig): void {
    // Throw only for authoring errors. A missing integration is a rendered
    // state, not a red error card — that tone is wrong for "you have not set
    // this up yet".
    if (config.entity && !config.entity.startsWith(ENTITY_PREFIX)) {
      throw new Error("`entity` must be a sensor");
    }
    this._config = config;
  }

  public getCardSize(): number {
    return 3;
  }

  public getGridOptions(): GridOptions {
    // Fixed height, deliberately. See the header comment.
    return { rows: 3, min_rows: 3, max_rows: 3, columns: 6, min_columns: 4 };
  }

  public static getStubConfig(): StatusCardConfig {
    // No entity. The card finds its own, so dropping it in just works.
    return { type: "custom:solar-sanity-card" };
  }

  /**
   * Every status entity this integration owns, in a stable order.
   *
   * Identified by what the entity publishes rather than by what it is called.
   * See `isStatusEntity`.
   */
  private get _candidates(): string[] {
    if (!this.hass) return [];
    const states = this.hass.states;
    return Object.keys(states)
      .filter((id) => id.startsWith(ENTITY_PREFIX) && isStatusEntity(states[id]))
      .sort();
  }

  private get _entity(): HassEntity | undefined {
    if (!this.hass) return undefined;
    if (this._config?.entity) return this.hass.states[this._config.entity];

    const found = this._candidates[0];
    return found ? this.hass.states[found] : undefined;
  }

  protected render(): TemplateResult {
    if (!this.hass) {
      return this._shell({
        glyph: "waiting",
        headline: "Connecting",
        body: "Waiting for Home Assistant.",
      });
    }

    if (this.hass.config.state !== "RUNNING") {
      return this._shell({
        glyph: "waiting",
        headline: "Home Assistant is still starting",
        body: "This will fill in by itself.",
      });
    }

    const candidates = this._candidates;
    if (!this._config?.entity && candidates.length > 1) {
      // Picking one silently would show a verdict about a house the reader may
      // not be looking at, with nothing on screen to say which.
      return this._shell({
        glyph: "unknown",
        headline: "More than one installation",
        body: `This card found ${candidates.length}. Set \`entity:\` to the one you want.`,
      });
    }

    const entity = this._entity;
    const status = entity?.state as Parameters<typeof verdictFor>[0];
    const attrs = (entity?.attributes ?? {}) as SolarSanityStatusAttributes;

    return this._shell(verdictFor(status, attrs));
  }

  private _shell(verdict: Verdict): TemplateResult {
    return html`
      <ha-card>
        <div class="root" data-glyph=${verdict.glyph}>
          <div class="row">
            <svg class="glyph" viewBox="0 0 24 24" aria-hidden="true">
              <path d=${GLYPHS[verdict.glyph]} />
            </svg>
            <h2 class="headline">${verdict.headline}</h2>
          </div>
          <p class="body" title=${verdict.full ?? nothing}>${verdict.body}</p>
          ${verdict.notes?.length
            ? html`<ul class="notes">
                ${verdict.notes.map((note) => html`<li>${note}</li>`)}
              </ul>`
            : nothing}
          ${verdict.action
            ? html`<button
                class="action"
                @click=${() => this._navigate(verdict.action!.href)}
              >
                ${verdict.action.label}
              </button>`
            : nothing}
        </div>
      </ha-card>
    `;
  }

  private _navigate(href: string): void {
    history.pushState(null, "", href);
    this.dispatchEvent(
      new CustomEvent("location-changed", { bubbles: true, composed: true }),
    );
  }

  static styles = css`
    :host {
      display: block;
    }
    .root {
      container-type: inline-size;
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-2, 8px);
      padding: var(--ha-space-4, 16px);
      min-height: 96px;
      box-sizing: border-box;
    }
    .notes {
      margin: 0;
      padding-left: var(--ha-space-4, 16px);
      display: flex;
      flex-direction: column;
      gap: var(--ha-space-2, 8px);
      color: var(--secondary-text-color);
      font-size: 0.9em;
      line-height: 1.4;
    }
    .row {
      display: flex;
      align-items: center;
      gap: var(--ha-space-3, 12px);
    }
    .glyph {
      width: 24px;
      height: 24px;
      flex: none;
      fill: var(--secondary-text-color);
    }
    /* Colour is the third signal, never the first. */
    [data-glyph="ok"] .glyph {
      fill: var(--success-color);
    }
    [data-glyph="alert"] .glyph {
      fill: var(--warning-color);
    }
    .headline {
      margin: 0;
      font-size: var(--ha-font-size-l, 1rem);
      font-weight: var(--ha-font-weight-medium, 500);
      color: var(--primary-text-color);
      line-height: 1.3;
      /* Logical properties throughout, so RTL mirrors for free. */
      text-align: start;
    }
    .body {
      margin: 0;
      color: var(--secondary-text-color);
      font-size: var(--ha-font-size-m, 0.875rem);
      line-height: 1.45;
      text-align: start;
      /* Belt and braces. The lead sentence keeps the text short enough for
         two lines at any reasonable width; this stops a narrow card or a
         large font pushing the action button out of a box that cannot
         grow. Backticks are avoided in here on purpose: this whole block
         is a tagged template, and one would end it. */
      display: -webkit-box;
      -webkit-box-orient: vertical;
      -webkit-line-clamp: 2;
      line-clamp: 2;
      overflow: hidden;
    }
    .action {
      align-self: start;
      margin-block-start: auto;
      padding: 6px 14px;
      min-height: 36px;
      border: 1px solid var(--divider-color);
      border-radius: var(--ha-border-radius-pill, 18px);
      background: transparent;
      color: var(--primary-color);
      font: inherit;
      cursor: pointer;
    }
    .action:hover {
      background: var(--secondary-background-color);
    }
    .action:focus-visible {
      outline: 2px solid var(--primary-color);
      outline-offset: 2px;
    }
    /* The card knows its own box; the viewport is the wrong instrument. */
    @container (max-width: 260px) {
      .body {
        /* Wins over the -webkit-box above; the headline and the button carry
           the card at this width. */
        display: none;
      }
    }
  `;
}
