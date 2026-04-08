/**
 * src/editor/NoteCompare.js
 * --------------------------
 * Note Compare & Correct panel.
 *
 * Compare a source (good) note against a destination (bad) note.
 * Shows per-parameter deviation from proportional base.
 * Allows correction (100%, partial %, copy missing partials).
 * Includes MIDI audition buttons for A/B listening.
 */

import api from "../comms/ApiClient.js";

export class NoteCompare {
    constructor(containerEl) {
        this._el = containerEl;
        this._srcMidi = 64;
        this._srcVel  = 4;
        this._dstMidi = 70;
        this._dstVel  = 4;
        this._velocity = 80;
        this._result = null;   // last compare result
        this._build();
    }

    // -- DOM ------------------------------------------------------------------

    _build() {
        this._el.innerHTML = `
<div style="padding:6px">
  <div style="font-weight:bold;margin-bottom:6px">Note Compare & Correct</div>

  <div style="display:flex;gap:12px;margin-bottom:8px">
    <div>
      <label style="font-size:11px;color:#8f8">SOURCE (good)</label><br>
      <input id="nc-src-midi" type="number" min="21" max="108" value="64"
             style="width:48px" title="MIDI note">
      <select id="nc-src-vel" style="width:54px">
        ${[0,1,2,3,4,5,6,7].map(v=>`<option value="${v}"${v===4?' selected':''}>vel ${v}</option>`).join('')}
      </select>
      <button id="nc-play-src" title="Play source note">&#9654;</button>
    </div>
    <div>
      <label style="font-size:11px;color:#f88">DESTINATION (fix)</label><br>
      <input id="nc-dst-midi" type="number" min="21" max="108" value="70"
             style="width:48px" title="MIDI note">
      <select id="nc-dst-vel" style="width:54px">
        ${[0,1,2,3,4,5,6,7].map(v=>`<option value="${v}"${v===4?' selected':''}>vel ${v}</option>`).join('')}
      </select>
      <button id="nc-play-dst" title="Play destination note">&#9654;</button>
    </div>
  </div>

  <div style="display:flex;gap:6px;margin-bottom:8px">
    <button id="nc-compare">Compare</button>
    <label style="font-size:11px;align-self:center">
      Vel: <input id="nc-vel-slider" type="range" min="1" max="127" value="80" style="width:60px">
      <span id="nc-vel-val">80</span>
    </label>
  </div>

  <div id="nc-table" style="max-height:400px;overflow-y:auto;font-size:11px"></div>

  <div style="margin-top:8px;display:flex;gap:6px">
    <button id="nc-apply-100" disabled title="Set all destination params to base (100% correction)">
      Apply 100%</button>
    <button id="nc-copy-missing" disabled title="Copy missing partials from source">
      Copy Missing</button>
    <button id="nc-send-bank" disabled title="Push corrected bank via SysEx">
      Send Bank &rarr;</button>
  </div>
</div>`;

        // Wire up events
        const $ = id => this._el.querySelector(id);

        $("#nc-compare").onclick = () => this._compare();

        $("#nc-play-src").onclick = () => {
            this._readInputs();
            api.audition(this._srcMidi, this._velocity).catch(e => console.warn(e));
        };
        $("#nc-play-dst").onclick = () => {
            this._readInputs();
            api.audition(this._dstMidi, this._velocity).catch(e => console.warn(e));
        };

        const velSlider = $("#nc-vel-slider");
        const velVal = $("#nc-vel-val");
        velSlider.oninput = () => {
            this._velocity = parseInt(velSlider.value);
            velVal.textContent = this._velocity;
        };

        $("#nc-apply-100").onclick = () => this._applyCorrection(0.0);
        $("#nc-copy-missing").onclick = () => this._copyMissing();
        $("#nc-send-bank").onclick = () => {
            api.sysexBank().then(() => console.log("Bank sent"))
                           .catch(e => console.warn(e));
        };
    }

    _readInputs() {
        const $ = id => this._el.querySelector(id);
        this._srcMidi = parseInt($("#nc-src-midi").value);
        this._srcVel  = parseInt($("#nc-src-vel").value);
        this._dstMidi = parseInt($("#nc-dst-midi").value);
        this._dstVel  = parseInt($("#nc-dst-vel").value);
    }

    // -- Compare --------------------------------------------------------------

    async _compare() {
        this._readInputs();
        try {
            this._result = await api.compareNotes(
                this._srcMidi, this._srcVel,
                this._dstMidi, this._dstVel);
            this._renderTable();
            // Enable action buttons
            const $ = id => this._el.querySelector(id);
            $("#nc-apply-100").disabled = false;
            $("#nc-copy-missing").disabled = false;
            $("#nc-send-bank").disabled = false;
        } catch (e) {
            this._el.querySelector("#nc-table").innerHTML =
                `<div style="color:#f66">${e.message}</div>`;
        }
    }

    _renderTable() {
        const r = this._result;
        if (!r) return;

        const rows = r.params.map(p => {
            const label = p.level === "partial" ? `${p.key} [k=${p.k}]` : p.key;
            const missing = p.missing || p.dst === null;

            const srcStr = p.src !== null ? p.src.toFixed(4) : "-";
            const baseStr = p.base !== null ? p.base.toFixed(4) : "-";
            const dstStr = missing ? "<em>missing</em>" : p.dst.toFixed(4);

            let devStr = "-";
            let devColor = "#ccc";
            if (!missing && p.deviation_pct !== null) {
                const d = p.deviation_pct;
                devStr = (d >= 0 ? "+" : "") + d.toFixed(1) + "%";
                if (Math.abs(d) > 20) devColor = "#f44";
                else if (Math.abs(d) > 10) devColor = "#fa4";
                else if (Math.abs(d) > 5) devColor = "#ff4";
                else devColor = "#4f4";
            }
            if (missing) { devStr = "MISSING"; devColor = "#f4f"; }

            return `<tr>
                <td>${label}</td>
                <td style="text-align:right">${srcStr}</td>
                <td style="text-align:right">${baseStr}</td>
                <td style="text-align:right">${dstStr}</td>
                <td style="text-align:right;color:${devColor};font-weight:bold">${devStr}</td>
            </tr>`;
        });

        const table = this._el.querySelector("#nc-table");
        table.innerHTML = `
<div style="margin-bottom:4px;color:#aaa">
  f0 ratio: ${r.f0_ratio.toFixed(4)}
  | src f0=${r.src.f0_hz?.toFixed(1)} Hz
  | dst f0=${r.dst.f0_hz?.toFixed(1)} Hz
</div>
<table style="width:100%;border-collapse:collapse">
<thead>
  <tr style="border-bottom:1px solid #555;color:#aaa">
    <th style="text-align:left">Param</th>
    <th style="text-align:right">Source</th>
    <th style="text-align:right">Base</th>
    <th style="text-align:right">Dest</th>
    <th style="text-align:right">Dev</th>
  </tr>
</thead>
<tbody>${rows.join("")}</tbody>
</table>`;
    }

    // -- Corrections ----------------------------------------------------------

    async _applyCorrection(pct) {
        if (!this._result) return;
        // Build corrections dict: set every param to base (pct=0)
        const corrections = {};
        for (const p of this._result.params) {
            if (p.missing || p.dst === null) continue;
            const key = p.level === "partial" ? `${p.key}_k${p.k}` : p.key;
            corrections[key] = pct;
        }
        try {
            const res = await api.correctNote(
                this._srcMidi, this._srcVel,
                this._dstMidi, this._dstVel,
                corrections, false);
            console.log("Applied:", res);
            await this._compare();  // refresh table
        } catch (e) {
            console.warn("Correct failed:", e);
        }
    }

    async _copyMissing() {
        if (!this._result) return;
        try {
            const res = await api.correctNote(
                this._srcMidi, this._srcVel,
                this._dstMidi, this._dstVel,
                {}, true);  // empty corrections, just copy missing
            console.log("Copied missing:", res);
            await this._compare();
        } catch (e) {
            console.warn("Copy failed:", e);
        }
    }
}
