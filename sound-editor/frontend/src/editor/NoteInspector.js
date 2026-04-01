/**
 * src/editor/NoteInspector.js
 * ────────────────────────────
 * Per-note inspector: B (inharmonicity) slider + EQ curve editor.
 *
 * B slider   → immediate SysEx SET_NOTE_PARAM when MIDI is connected
 * EQ canvas  → drag frequency points; "Apply EQ" refits biquads in backend
 *              + prompts user to send bank if MIDI connected
 */

import api from "../comms/ApiClient.js";

const EQ_W    = 256;   // canvas width
const EQ_H    = 120;   // canvas height
const EQ_PAD  = 6;     // inner padding
const DB_RANGE = 12;   // ±12 dB

export class NoteInspector {
    constructor(containerEl) {
        this._el    = containerEl;
        this._midi  = 60;
        this._vel   = 3;
        this._B     = 0.0;
        this._freqs = [];
        this._gains = [];
        this._drag  = null;   // index of dragged point, or null
        this._canvas = null;
        this._ctx    = null;
        this._build();
    }

    // ── DOM construction ──────────────────────────────────────────────────────

    _build() {
        this._el.innerHTML = `
<div style="display:flex;gap:4px;margin-bottom:4px">
  <input id="ni-midi" type="number" min="21" max="108" value="60"
         style="width:52px" title="MIDI note">
  <select id="ni-vel" style="flex:1">
    ${[0,1,2,3,4,5,6,7].map(v=>`<option value="${v}"${v===3?' selected':''}>vel ${v}</option>`).join('')}
  </select>
  <button id="ni-load" style="width:auto;padding:2px 8px">Load</button>
</div>

<div id="ni-b-row" style="display:none">
  <label style="margin-top:4px">
    B (inharmonicity)
    <div style="display:flex;align-items:center;gap:4px">
      <input type="range" id="ni-b-slider" min="0" max="0.005" step="0.000001" value="0"
             style="flex:1">
      <span id="ni-b-val" style="color:#aaf;min-width:64px;text-align:right;font-size:10px">0.000000</span>
    </div>
  </label>
  <button id="ni-b-send" style="margin-top:2px">Send B via SysEx</button>
</div>

<div id="ni-eq-row" style="display:none;margin-top:6px">
  <div style="font-size:10px;color:#889;margin-bottom:3px">EQ curve — drag points</div>
  <canvas id="ni-eq-canvas" width="${EQ_W}" height="${EQ_H}"
          style="display:block;background:#0a0a18;border:1px solid #224;cursor:crosshair"></canvas>
  <div style="display:flex;gap:4px;margin-top:3px">
    <button id="ni-eq-reset" style="flex:1">Reset</button>
    <button id="ni-eq-apply" class="primary" style="flex:1">Apply EQ</button>
  </div>
  <div id="ni-eq-info" style="font-size:10px;color:#556;margin-top:2px"></div>
</div>`;

        this._setupListeners();
    }

    _setupListeners() {
        this._el.querySelector("#ni-load").addEventListener("click", () => this._load());

        const slider = this._el.querySelector("#ni-b-slider");
        slider.addEventListener("input", () => {
            this._B = parseFloat(slider.value);
            this._el.querySelector("#ni-b-val").textContent = this._B.toFixed(6);
        });

        this._el.querySelector("#ni-b-send").addEventListener("click", () => this._sendB());
        this._el.querySelector("#ni-eq-reset").addEventListener("click", () => this._resetGains());
        this._el.querySelector("#ni-eq-apply").addEventListener("click", () => this._applyEQ());

        // Canvas interaction set up after canvas is confirmed present
        requestAnimationFrame(() => {
            this._canvas = this._el.querySelector("#ni-eq-canvas");
            if (!this._canvas) return;
            this._ctx = this._canvas.getContext("2d");
            this._canvas.addEventListener("mousedown", e => this._onDown(e));
            this._canvas.addEventListener("mousemove", e => this._onMove(e));
            this._canvas.addEventListener("mouseup",   () => this._drag = null);
            this._canvas.addEventListener("mouseleave",() => this._drag = null);
        });
    }

    // ── Load note data ────────────────────────────────────────────────────────

    async _load() {
        this._midi = parseInt(this._el.querySelector("#ni-midi").value);
        this._vel  = parseInt(this._el.querySelector("#ni-vel").value);
        try {
            const data = await api.getEq(this._midi, this._vel);
            this._B     = 0.0;  // B comes from note scalar layer, not EQ endpoint
            this._freqs = data.freqs_hz ?? [];
            this._gains = [...(data.gains_db ?? [])];
            this._origGains = [...this._gains];

            // Try loading B from layer values
            try {
                const vals = await api.getLayerValues("B");
                const key  = `m${String(this._midi).padStart(3,'0')}_vel${this._vel}`;
                if (key in vals) {
                    this._B = vals[key];
                }
            } catch (_) {}

            this._showPanels();
            this._drawEQ();
            _setInfo(this._el, `Loaded m${this._midi} vel${this._vel}`);
        } catch (err) {
            _setInfo(this._el, `Load error: ${err.message}`, true);
        }
    }

    _showPanels() {
        const slider = this._el.querySelector("#ni-b-slider");
        slider.value = this._B;
        this._el.querySelector("#ni-b-val").textContent = this._B.toFixed(6);
        this._el.querySelector("#ni-b-row").style.display = "block";
        this._el.querySelector("#ni-eq-row").style.display =
            this._freqs.length ? "block" : "none";
    }

    // ── B via SysEx ───────────────────────────────────────────────────────────

    async _sendB() {
        try {
            await api.sysexNote(this._midi, this._vel, "B", this._B);
            _setInfo(this._el, `B=${this._B.toFixed(6)} sent`);
        } catch (err) {
            _setInfo(this._el, `SysEx error: ${err.message}`, true);
        }
    }

    // ── EQ apply ─────────────────────────────────────────────────────────────

    async _applyEQ() {
        if (!this._freqs.length) return;
        try {
            const r = await api.updateEq(this._midi, this._vel, this._freqs, this._gains);
            this._origGains = [...this._gains];
            _setInfo(this._el,
                `EQ updated (${r.n_biquads} biquads). Use "Send bank →" to push to synth.`);
        } catch (err) {
            _setInfo(this._el, `Apply error: ${err.message}`, true);
        }
    }

    _resetGains() {
        if (!this._origGains) return;
        this._gains = [...this._origGains];
        this._drawEQ();
    }

    // ── Canvas helpers ────────────────────────────────────────────────────────

    /** Map log-frequency to canvas X */
    _fToX(f) {
        const lo = Math.log(20), hi = Math.log(22050);
        return EQ_PAD + (Math.log(f) - lo) / (hi - lo) * (EQ_W - 2 * EQ_PAD);
    }

    /** Map dB gain to canvas Y */
    _dbToY(db) {
        return EQ_PAD + (1 - (db + DB_RANGE) / (2 * DB_RANGE)) * (EQ_H - 2 * EQ_PAD);
    }

    /** Map canvas Y to dB */
    _yToDb(y) {
        const t = 1 - (y - EQ_PAD) / (EQ_H - 2 * EQ_PAD);
        return t * 2 * DB_RANGE - DB_RANGE;
    }

    _drawEQ() {
        if (!this._ctx || !this._freqs.length) return;
        const ctx = this._ctx;
        ctx.clearRect(0, 0, EQ_W, EQ_H);

        // Grid
        ctx.strokeStyle = "#1a1a2a";
        ctx.lineWidth = 1;
        // 0dB line
        ctx.beginPath();
        const y0 = this._dbToY(0);
        ctx.moveTo(EQ_PAD, y0); ctx.lineTo(EQ_W - EQ_PAD, y0);
        ctx.strokeStyle = "#2a2a4a"; ctx.stroke();
        // ±6dB
        for (const db of [-6, 6]) {
            ctx.beginPath();
            const y = this._dbToY(db);
            ctx.moveTo(EQ_PAD, y); ctx.lineTo(EQ_W - EQ_PAD, y);
            ctx.strokeStyle = "#181828"; ctx.stroke();
        }
        // Octave verticals (125, 250, 500, 1k, 2k, 4k, 8k, 16k Hz)
        ctx.strokeStyle = "#181828";
        for (const f of [125, 250, 500, 1000, 2000, 4000, 8000, 16000]) {
            const x = this._fToX(f);
            ctx.beginPath(); ctx.moveTo(x, EQ_PAD); ctx.lineTo(x, EQ_H - EQ_PAD);
            ctx.stroke();
        }

        // Curve
        ctx.beginPath();
        ctx.strokeStyle = "#5af";
        ctx.lineWidth = 1.5;
        for (let i = 0; i < this._freqs.length; i++) {
            const x = this._fToX(this._freqs[i]);
            const y = this._dbToY(this._gains[i]);
            i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.stroke();

        // Points
        for (let i = 0; i < this._freqs.length; i++) {
            const x = this._fToX(this._freqs[i]);
            const y = this._dbToY(this._gains[i]);
            ctx.beginPath();
            ctx.arc(x, y, i === this._drag ? 5 : 3, 0, Math.PI * 2);
            ctx.fillStyle = i === this._drag ? "#fff" : "#5af";
            ctx.fill();
        }

        // dB label at cursor point
        if (this._drag !== null) {
            const db = this._gains[this._drag];
            const x  = this._fToX(this._freqs[this._drag]);
            const y  = this._dbToY(db);
            ctx.fillStyle = "#ccc";
            ctx.font = "9px monospace";
            ctx.fillText(`${db >= 0 ? "+" : ""}${db.toFixed(1)}dB`, x + 6, y - 4);
        }
    }

    _onDown(e) {
        const r = this._canvas.getBoundingClientRect();
        const mx = e.clientX - r.left, my = e.clientY - r.top;
        let best = 10, bestIdx = null;
        for (let i = 0; i < this._freqs.length; i++) {
            const dx = this._fToX(this._freqs[i]) - mx;
            const dy = this._dbToY(this._gains[i]) - my;
            const d  = Math.sqrt(dx * dx + dy * dy);
            if (d < best) { best = d; bestIdx = i; }
        }
        this._drag = bestIdx;
    }

    _onMove(e) {
        if (this._drag === null) return;
        const r  = this._canvas.getBoundingClientRect();
        const my = e.clientY - r.top;
        this._gains[this._drag] = Math.max(-DB_RANGE,
            Math.min(DB_RANGE, this._yToDb(my)));
        this._drawEQ();
    }
}

function _setInfo(el, msg, isError = false) {
    const d = el.querySelector("#ni-eq-info");
    if (d) { d.textContent = msg; d.style.color = isError ? "#f66" : "#556"; }
}
