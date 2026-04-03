"""
training/modules/profile_trainer_no_b.py
──────────────────────────────────────────
Variant of InstrumentProfileEncExp that does not predict B (inharmonicity).

Why:
    B is physically velocity-independent (string stiffness) and its extracted
    values have high variance, causing B loss to dominate the multi-task loss
    (~5–8× other terms).  This module replaces B prediction with an external
    BSplneFitter: a smooth 1-D spline over MIDI fitted from measured data.

What changes vs InstrumentProfileEncExp:
    - B_head removed (frees ~1 K params + optimizer state)
    - Shared midi_enc / vel_enc trained by 10 heads instead of 11
    - forward_B returns zeros → smooth penalty term for B is zero (correct:
      B is smooth by definition from the spline)
    - B tensors excluded from dataset → B absent from loss computation
      (_compute_data_loss_exp checks ``if "B_mf" in b:`` already)

Everything else (architecture, training loop, ICR eval, expand) is reused
unchanged from profile_trainer_exp.

Public API:
    from training.modules.b_spline_fitter import BSplneFitter

    b_fitter = BSplneFitter().fit(params["samples"])
    model    = ProfileTrainerNoB().train(params, b_fitter=b_fitter, epochs=5000)
    samples  = generate_profile_exp_no_b(model, ds, b_fitter,
                                          midi_from=21, midi_to=108, sr=sr,
                                          orig_samples=measured)
"""

from __future__ import annotations

import copy
import math

import numpy as np
import torch
import torch.nn as nn

from training.modules.profile_trainer import (
    _split_val_midis,
    midi_feat, vel_feat, k_feat, freq_feat, midi_to_hz,
)
from training.modules.profile_trainer_exp import (
    InstrumentProfileEncExp,
    ProfileTrainerEncExp,
    build_dataset_exp,
    _run_training_exp,
)
from training.modules.b_spline_fitter import BSplneFitter


# ─────────────────────────────────────────────────────────────────────────────
# Dataset — B columns excluded
# ─────────────────────────────────────────────────────────────────────────────

def build_dataset_exp_no_b(samples: dict) -> dict:
    """
    Like build_dataset_exp but removes B_mf / B_vf / B_y from the batch dict.

    Since _compute_data_loss_exp checks ``if "B_mf" in b:`` before computing
    the B loss term, simply removing these keys is sufficient to eliminate B
    from the gradient entirely.
    """
    ds = build_dataset_exp(samples)
    for key in ("B_mf", "B_vf", "B_y"):
        ds["batches"].pop(key, None)
    ds["n_B"] = 0
    return ds


# ─────────────────────────────────────────────────────────────────────────────
# Model — B_head removed
# ─────────────────────────────────────────────────────────────────────────────

class InstrumentProfileEncExpNoB(InstrumentProfileEncExp):
    """
    InstrumentProfileEncExp with B_head removed.

    B_head is deleted from the module so its parameters do not consume
    optimizer state or contribute to gradients.

    forward_B returns a zero tensor so the MIDI/velocity smoothness penalties
    that call forward_B in _run_training_exp produce zero contribution for B
    (which is correct: B is smooth by definition via the spline).
    """

    def __init__(self, hidden: int = 64, head_hidden: int = 32):
        super().__init__(hidden=hidden, head_hidden=head_hidden)
        del self.B_head

    def forward_B(self, mf, vf=None):
        """Return zeros — B smoothness penalty contribution is zero."""
        n = mf.shape[0] if mf.dim() > 1 else 1
        return torch.zeros(n, 1, dtype=mf.dtype, device=mf.device)


# ─────────────────────────────────────────────────────────────────────────────
# Profile generation — B filled from spline
# ─────────────────────────────────────────────────────────────────────────────

def generate_profile_exp_no_b(
    model:        InstrumentProfileEncExpNoB,
    ds:           dict,
    b_fitter:     BSplneFitter,
    midi_from:    int  = 21,
    midi_to:      int  = 108,
    sr:           int  = 44_100,
    orig_samples: dict = None,
) -> dict:
    """
    Like generate_profile_exp but B is sourced from b_fitter (spline over MIDI)
    instead of the NN.

    B is evaluated once per MIDI and reused for all 8 velocity layers —
    consistent with its physical interpretation as velocity-independent.

    Args:
        model:        Trained InstrumentProfileEncExpNoB.
        ds:           Dataset dict (used only for eq_freqs).
        b_fitter:     Fitted BSplneFitter.
        midi_from:    First MIDI note to generate (inclusive).
        midi_to:      Last MIDI note to generate (inclusive).
        sr:           Sample rate (determines max partial count per note).
        orig_samples: If given, measured (non-interpolated) notes are copied
                      verbatim; NN + spline B is used only for missing slots.

    Returns:
        Dict keyed "m{midi:03d}_vel{vel}" → sample dict (same format as
        generate_profile_exp output).
    """
    model.eval()
    samples_out = {}
    eq_freqs    = ds.get("eq_freqs")

    with torch.no_grad():
        for midi in range(midi_from, midi_to + 1):
            mf = midi_feat(midi)
            f0 = midi_to_hz(midi)
            n_partials = max(1, int((sr / 2) / f0))

            # B from spline — same for all velocities at this MIDI
            B = b_fitter.predict(midi)

            for vel in range(8):
                vf  = vel_feat(vel)
                key = f"m{midi:03d}_vel{vel}"

                dur = float(np.clip(float(torch.exp(model.forward_dur(mf, vf)).item()), 0.3, None))
                wf  = float(np.clip(float(torch.exp(model.forward_wf(mf, vf)).item()),  0.1, 10.0))

                spectral_eq: dict = {}
                if eq_freqs is not None:
                    gains = [
                        float(np.clip(float(model.forward_eq(
                            mf, freq_feat(float(fhz)), vf).item()), -30, 20))
                        for fhz in eq_freqs
                    ]
                    spectral_eq = {
                        "freqs_hz":            [round(float(f), 2) for f in eq_freqs],
                        "gains_db":            [round(g, 4) for g in gains],
                        "stereo_width_factor": round(wf, 4),
                    }

                noise_pred = model.forward_noise(mf, vf).squeeze(0)
                noise_out  = {
                    "attack_tau":  round(float(np.clip(float(torch.exp(noise_pred[0]).item()), 0.002, 1.0)), 5),
                    "centroid_hz": round(float(np.clip(float(torch.exp(noise_pred[1]).item()), 100.0, 20000.0)), 1),
                    "A_noise":     round(float(np.clip(float(torch.exp(noise_pred[2]).item()), 0.001, 0.5)), 5),
                }

                partials = []
                for k in range(1, n_partials + 1):
                    kf  = k_feat(k)
                    f_k = k * f0 * math.sqrt(1.0 + B * k ** 2)
                    if f_k >= sr / 2:
                        break

                    tau1_k1 = float(torch.exp(model.forward_tau1_k1(mf, vf)).item())
                    if k == 1:
                        tau1 = tau1_k1
                    else:
                        log_ratio  = float(model.forward_tau_ratio(mf, kf, vf).item())
                        log_k_bias = -0.3 * math.log(k)
                        log_ratio  = max(log_k_bias - 2.0, min(0.0, log_ratio))
                        tau1 = tau1_k1 * math.exp(log_ratio)
                    tau1 = max(tau1, 0.005)

                    biexp    = model.forward_biexp(mf, kf, vf).squeeze(0)
                    a1_val   = float(np.clip(float(torch.sigmoid(biexp[0]).item()), 0.05, 0.99))
                    tau2_val = tau1 * max(float(torch.exp(biexp[1]).item()), 3.0)
                    emit_biexp = a1_val < 0.92

                    a0_ratio = float(np.clip(float(torch.exp(model.forward_A0(mf, kf, vf)).item()), 1e-6, None))
                    df       = float(np.clip(float(torch.exp(model.forward_df(mf, kf, vf)).item()), 0.0, None))

                    entry = {
                        "k": k, "f_hz": round(f_k, 4),
                        "A0": round(a0_ratio, 6), "tau1": round(tau1, 6),
                        "a1": round(a1_val, 4), "beat_hz": round(df, 6),
                    }
                    if emit_biexp:
                        entry["tau2"] = round(tau2_val, 6)
                    partials.append(entry)

                sample = {
                    "midi": midi, "vel": vel,
                    "f0_nominal_hz": round(f0, 6),
                    "B": round(float(B), 8), "duration_s": round(float(dur), 3),
                    "partials": partials, "noise": noise_out,
                    "_interpolated": True,
                }
                if spectral_eq:
                    sample["spectral_eq"] = spectral_eq

                if orig_samples and key in orig_samples and not orig_samples[key].get("_interpolated"):
                    sample = copy.deepcopy(orig_samples[key])
                    sample["_interpolated"] = False

                samples_out[key] = sample

    return samples_out


# ─────────────────────────────────────────────────────────────────────────────
# Trainer
# ─────────────────────────────────────────────────────────────────────────────

class ProfileTrainerNoB(ProfileTrainerEncExp):
    """
    Train InstrumentProfileEncExpNoB — like ProfileTrainerEncExp but B is
    excluded from NN targets, gradient, and optimizer state.

    B must be supplied as a fitted BSplneFitter.  It is used only during
    profile generation, not during training.

    API:
        b_fitter = BSplneFitter().fit(params["samples"])
        model    = ProfileTrainerNoB().train(params, b_fitter=b_fitter, epochs=5000)
        samples  = generate_profile_exp_no_b(model, ds, b_fitter, orig_samples=measured)
    """

    def train(
        self,
        params:        dict,
        b_fitter:      BSplneFitter,
        epochs:        int   = 10000,
        hidden:        int   = 64,
        lr:            float = 0.003,
        val_frac:      float = 0.15,
        verbose:       bool  = True,
        icr_evaluator         = None,
        icr_patience:  int   = 15,
    ) -> InstrumentProfileEncExpNoB:
        samples  = params["samples"]
        measured = {k: v for k, v in samples.items() if not v.get("_interpolated")}

        train_s, val_s = _split_val_midis(measured, val_frac)
        val_midis = sorted({s["midi"] for s in val_s.values()})
        enc = InstrumentProfileEncExp
        print(
            f"ProfileTrainerNoB: {len(measured)} measured samples  "
            f"→  train={len(train_s)}  val={len(val_s)} "
            f"(MIDI {val_midis[0]}–{val_midis[-1]}, every ~{len(measured)//max(len(val_s),1)}th)"
        )
        print(
            f"  Mode: no-B enc — B from spline (not trained), shared encoders "
            f"(midi→{enc.ENC_MIDI}  vel→{enc.ENC_VEL}  k→{enc.ENC_K}  freq→{enc.ENC_FREQ})"
        )
        if icr_evaluator is not None:
            print(f"  Eval: ICR-MRSTFT (early stop patience={icr_patience} evals, no MRSTFTFinetuner)")

        print("Building datasets (B excluded) ...", flush=True)
        train_ds = build_dataset_exp_no_b(train_s)
        val_ds   = build_dataset_exp_no_b(val_s)
        _ds_sizes = {k: v.shape[0] for k, v in train_ds["batches"].items() if hasattr(v, "shape")}
        print(f"  train batches: { {k: v for k, v in _ds_sizes.items()} }", flush=True)

        model = InstrumentProfileEncExpNoB(hidden=hidden, head_hidden=32)
        n_p   = sum(p.numel() for p in model.parameters())
        print(f"Model parameters: {n_p:,}  (no B_head, encoders hidden={hidden}, heads hidden=32)", flush=True)

        if icr_evaluator is not None:
            print(f"Training {epochs} epochs  (ICR-MRSTFT early stop) ...", flush=True)
        else:
            print(f"Training {epochs} epochs  (progressive depth: expand heads at plateau) ...", flush=True)

        _run_training_exp(
            model, train_ds, val_ds=val_ds,
            epochs=epochs, lr=lr, verbose=verbose,
            all_params=params,
            icr_evaluator=icr_evaluator,
            icr_patience=icr_patience,
        )
        model.eval()
        return model
