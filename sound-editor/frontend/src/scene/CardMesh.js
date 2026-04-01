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

// Sphere radius — small enough that 88 notes don't overlap
const SPHERE_R = 0.06;

const MAT_DEFAULT = new THREE.MeshStandardMaterial({
    color: 0x2a6080, roughness: 0.4, metalness: 0.5,
    emissive: 0x1a4060, emissiveIntensity: 0.8,
});
const MAT_HOVER   = new THREE.MeshStandardMaterial({
    color: 0x66ddff, roughness: 0.2, metalness: 0.6,
    emissive: 0x33aacc, emissiveIntensity: 1.2,
});
const MAT_ANCHOR  = new THREE.MeshStandardMaterial({
    color: 0xffcc33, roughness: 0.2, metalness: 0.5,
    emissive: 0xaa6600, emissiveIntensity: 1.0,
});
const GEOM_SPHERE = new THREE.SphereGeometry(SPHERE_R, 8, 6);

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

        this.mesh = new THREE.Mesh(GEOM_SPHERE, isAnchor ? MAT_ANCHOR : MAT_DEFAULT);
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
        // Shared geometry/materials — no disposal needed here.
    }
}
