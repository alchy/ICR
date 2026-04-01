/**
 * src/editor/VelSelector.js
 * ─────────────────────────
 * Top-bar velocity selector + spline controllers.
 *
 * Layout (centered, top of 3D canvas):
 *   ①②③④⑤⑥⑦⑧   [Coherence ──i──]   [Stickiness ──i──]
 *
 * Circles are 1-based (display 1–8, internal 0–7).
 * Multiple circles can be selected; controllers adapt to selection count.
 */

// Colours per velocity (matching SplineMesh tones)
const VEL_COLORS = [
    "#4af", "#4fa", "#fa4", "#f4a",
    "#af4", "#a4f", "#ff8", "#8ff",
];

export class VelSelector {
    /**
     * @param {HTMLElement} container   element to render into
     * @param {Function}    onChange    ({ selected: Set<int>, coherence, stickiness }) => void
     */
    constructor(container, onChange) {
        this._container = container;
        this._onChange  = onChange;
        this.selected   = new Set([0, 1, 2, 3, 4, 5, 6, 7]);   // all on by default
        this.coherence  = 0.0;
        this.stickiness = 3.0;
        this._build();
    }

    velColor(vel) { return VEL_COLORS[vel % VEL_COLORS.length]; }

    _build() {
        this._container.innerHTML = `
            <style>
            #vel-bar {
                display: flex; align-items: center; justify-content: center; gap: 6px;
                padding: 6px 14px;
            }
            .vc {
                width: 28px; height: 28px; border-radius: 50%;
                border: 2px solid; display: inline-flex; align-items: center;
                justify-content: center; font-size: 12px; font-weight: bold;
                cursor: pointer; user-select: none; transition: opacity .15s;
                font-family: monospace;
            }
            .vc.off { opacity: 0.22; }
            .vel-ctrl {
                display: flex; align-items: center; gap: 5px;
                background: rgba(0,0,0,.5); border: 1px solid #334;
                padding: 3px 8px; font-size: 11px; color: #889;
                white-space: nowrap;
            }
            .vel-ctrl input[type=range] { width: 90px; accent-color: #4af; }
            .vel-ctrl span.val { min-width: 32px; color: #aac; }
            #ctrl-stickiness { display: none; }
            </style>

            <div id="vel-bar">
                <div id="vel-circles"></div>
                <div class="vel-ctrl" id="ctrl-coherence">
                    Coherence
                    <input type="range" id="sl-coherence" min="0" max="1" step="0.01" value="0">
                    <span class="val" id="lbl-coherence">0.00</span>
                </div>
                <div class="vel-ctrl" id="ctrl-stickiness">
                    Stickiness
                    <input type="range" id="sl-stickiness" min="0.1" max="10" step="0.1" value="3">
                    <span class="val" id="lbl-stickiness">3.0</span>
                </div>
            </div>
        `;

        // Circles
        const wrap = this._container.querySelector("#vel-circles");
        wrap.style.cssText = "display:flex;gap:4px;";
        for (let vel = 0; vel < 8; vel++) {
            const c   = document.createElement("div");
            c.className   = "vc";
            c.textContent = String(vel + 1);
            c.style.borderColor = VEL_COLORS[vel];
            c.style.color       = VEL_COLORS[vel];
            c.dataset.vel       = vel;
            c.addEventListener("click", () => this._toggleVel(vel, c));
            wrap.appendChild(c);
        }

        // Coherence slider
        this._container.querySelector("#sl-coherence").addEventListener("input", e => {
            this.coherence = parseFloat(e.target.value);
            this._container.querySelector("#lbl-coherence").textContent =
                this.coherence.toFixed(2);
            this._emit();
        });

        // Stickiness slider
        this._container.querySelector("#sl-stickiness").addEventListener("input", e => {
            this.stickiness = parseFloat(e.target.value);
            this._container.querySelector("#lbl-stickiness").textContent =
                this.stickiness.toFixed(1);
            this._emit();
        });

        this._updateUI();
    }

    _toggleVel(vel, el) {
        if (this.selected.has(vel)) {
            if (this.selected.size === 1) return;   // keep at least one
            this.selected.delete(vel);
            el.classList.add("off");
        } else {
            this.selected.add(vel);
            el.classList.remove("off");
        }
        this._updateUI();
        this._emit();
    }

    _updateUI() {
        // Show stickiness only when exactly one velocity selected
        const single = this.selected.size === 1;
        this._container.querySelector("#ctrl-stickiness").style.display =
            single ? "flex" : "none";
    }

    _emit() {
        this._onChange({
            selected:   this.selected,
            coherence:  this.coherence,
            stickiness: this.stickiness,
        });
    }

    /** Programmatically update stickiness (e.g. after loading spline state). */
    setStickiness(v) {
        this.stickiness = v;
        const sl = this._container.querySelector("#sl-stickiness");
        if (sl) { sl.value = v; }
        const lbl = this._container.querySelector("#lbl-stickiness");
        if (lbl) lbl.textContent = v.toFixed(1);
    }
}

export { VEL_COLORS };
