/**
 * src/scene/CardMesh.js
 * ─────────────────────
 * One card = one (midi, vel) data point in 3D space.
 *
 * Layout:
 *   X → MIDI note   (21 – 108)
 *   Y → parameter value
 *   Z → velocity    (0 – 7)
 */

import * as THREE from "three";

const CARD_W  = 0.55;
const CARD_H  = 0.20;
const CARD_D  = 0.04;

const MAT_DEFAULT = new THREE.MeshStandardMaterial({
    color: 0x2a6080, roughness: 0.5, metalness: 0.4,
    emissive: 0x0a2030, emissiveIntensity: 0.6,
});
const MAT_HOVER   = new THREE.MeshStandardMaterial({
    color: 0x44aadd, roughness: 0.3, metalness: 0.6,
    emissive: 0x1a5070, emissiveIntensity: 1.0,
});
const MAT_ANCHOR  = new THREE.MeshStandardMaterial({
    color: 0xddaa22, roughness: 0.3, metalness: 0.5,
    emissive: 0x664400, emissiveIntensity: 0.8,
});
const GEOM_CARD   = new THREE.BoxGeometry(CARD_W, CARD_H, CARD_D);

export class CardMesh {
    /**
     * @param {Object} opts
     * @param {number} opts.midi      MIDI note (21–108)
     * @param {number} opts.vel       velocity layer (0–7)
     * @param {number} opts.value     parameter value
     * @param {string} opts.label     note + vel label
     * @param {boolean} opts.isAnchor anchor flag
     */
    constructor({ midi, vel, value, label = "", isAnchor = false }) {
        this.midi     = midi;
        this.vel      = vel;
        this.value    = value;
        this.label    = label;
        this.isAnchor = isAnchor;
        this._hovered = false;

        this.mesh = new THREE.Mesh(GEOM_CARD, isAnchor ? MAT_ANCHOR : MAT_DEFAULT);
        this.mesh.userData.card = this;
        this.mesh.castShadow    = true;
    }

    /** Place card at world position. */
    setPosition(x, y, z) {
        this.mesh.position.set(x, y, z);
    }

    setHover(on) {
        if (this._hovered === on) return;
        this._hovered = on;
        this.mesh.material = on
            ? MAT_HOVER
            : (this.isAnchor ? MAT_ANCHOR : MAT_DEFAULT);
    }

    setAnchor(on) {
        this.isAnchor = on;
        if (!this._hovered)
            this.mesh.material = on ? MAT_ANCHOR : MAT_DEFAULT;
    }

    dispose() {
        // Shared geometries/materials — no disposal needed here.
    }
}
