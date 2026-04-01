/**
 * src/scene/SplineMesh.js
 * ───────────────────────
 * Renders one velocity layer's fitted spline as a tube + endpoint rings.
 *
 *  (vel+1)●────────────────────────────────────●(vel+1)
 *          MIDI 21                          MIDI 108
 */

import * as THREE from "three";

// ── Label sprite ──────────────────────────────────────────────────────────────

function makeVelLabel(num, color) {
    const size   = 64;
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    const ctx    = canvas.getContext("2d");

    ctx.fillStyle   = "#000a";
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - 2, 0, Math.PI * 2);
    ctx.fill();

    ctx.strokeStyle = color;
    ctx.lineWidth   = 3;
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - 3, 0, Math.PI * 2);
    ctx.stroke();

    ctx.fillStyle   = color;
    ctx.font        = "bold 28px monospace";
    ctx.textAlign   = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(String(num), size / 2, size / 2);

    const tex = new THREE.CanvasTexture(canvas);
    const mat = new THREE.SpriteMaterial({ map: tex, depthTest: false, transparent: true });
    const spr = new THREE.Sprite(mat);
    spr.scale.set(0.4, 0.4, 1);
    spr.renderOrder = 998;
    return spr;
}

// ── SplineMesh ────────────────────────────────────────────────────────────────

export class SplineMesh {
    /**
     * @param {string}   colorHex
     * @param {number}   vel        velocity index 0–7
     * @param {Function} xFromMidi  midi → world X
     * @param {Function} yFromVal   value → world Y
     */
    constructor(colorHex, vel, xFromMidi, yFromVal) {
        this._colorHex  = colorHex;
        this._vel       = vel;
        this._xFromMidi = xFromMidi;
        this._yFromVal  = yFromVal;
        this._objects   = [];   // all Three objects owned by this instance

        this._mat = new THREE.MeshStandardMaterial({
            color:             colorHex,
            emissive:          colorHex,
            emissiveIntensity: 0.4,
            roughness:         0.5,
            metalness:         0.2,
        });
        this._ghostMat = new THREE.MeshStandardMaterial({
            color:       0x445566,
            transparent: true,
            opacity:     0.28,
            roughness:   0.8,
        });
    }

    /**
     * Rebuild tube + endpoint rings from curve data.
     * @param {number[]} xMidi   dense MIDI positions
     * @param {number[]} yVals   fitted values
     * @param {THREE.Scene} scene
     */
    update(xMidi, yVals, scene) {
        this._clear(scene);
        if (!xMidi || xMidi.length < 2) return;

        const z    = this._vel * 1.2;
        const num  = this._vel + 1;   // 1-based display number
        const col  = this._colorHex;

        // ── Tube ─────────────────────────────────────────────────────────────
        const points = xMidi.map((m, i) =>
            new THREE.Vector3(this._xFromMidi(m), this._yFromVal(yVals[i]), z)
        );
        const curve = new THREE.CatmullRomCurve3(points);
        const geom  = new THREE.TubeGeometry(curve, points.length * 2, 0.025, 6, false);
        const tube  = new THREE.Mesh(geom, this._mat);
        tube.userData.isSpline = true;
        this._add(tube, scene);

        // ── Endpoint rings + labels ───────────────────────────────────────────
        const ringGeo = new THREE.TorusGeometry(0.12, 0.025, 8, 24);
        const ringMat = new THREE.MeshStandardMaterial({
            color: col, emissive: col, emissiveIntensity: 0.8,
        });

        for (const pt of [points[0], points[points.length - 1]]) {
            const ring = new THREE.Mesh(ringGeo, ringMat);
            ring.position.copy(pt);
            ring.rotation.x = Math.PI / 2;   // face camera (roughly)
            this._add(ring, scene);

            const lbl = makeVelLabel(num, col);
            lbl.position.set(pt.x, pt.y + 0.30, pt.z);
            this._add(lbl, scene);
        }
    }

    _add(obj, scene) {
        scene.add(obj);
        this._objects.push(obj);
    }

    _clear(scene) {
        for (const o of this._objects) {
            scene.remove(o);
            o.geometry?.dispose();
            if (o.material && o.material !== this._mat) o.material.dispose();
            if (o.isSprite) o.material?.map?.dispose();
        }
        this._objects = [];
    }

    dispose(scene) {
        this._clear(scene);
        this._mat.dispose();
        this._ghostMat.dispose();
    }
}
