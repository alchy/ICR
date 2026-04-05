"""
training/modules/profile_trainer.py
──────────────────────────────────────
Train a smooth InstrumentProfile neural network from extracted params.

Public API:
    trainer = ProfileTrainer()
    model   = trainer.train(params, epochs=1800, hidden=64, lr=0.003)
    model   = trainer.load("profile.pt")
    pred    = trainer.predict_all(model)   # → params dict for all 88×8 notes

This module also exports InstrumentProfile and its feature encoders so other
modules (mrstft_finetune, synthesizer) can import them from here.
"""

import copy
import math
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim


# ─────────────────────────────────────────────────────────────────────────────
# Feature encoders (shared across all sub-networks)
# ─────────────────────────────────────────────────────────────────────────────

MIDI_DIM = 6
VEL_DIM  = 3
K_DIM    = 3
FREQ_DIM = 2


def midi_to_hz(midi: float) -> float:
    return 440.0 * 2.0**((midi - 69) / 12.0)


def midi_feat(midi: float) -> torch.Tensor:
    """Normalised MIDI + sinusoidal embedding for register awareness."""
    m = (midi - 21) / 87.0
    return torch.tensor([
        m,
        math.sin(math.pi  * m), math.sin(2*math.pi * m), math.sin(4*math.pi * m),
        math.cos(math.pi  * m), math.cos(2*math.pi * m),
    ], dtype=torch.float32)


def vel_feat(vel: int) -> torch.Tensor:
    v = vel / 7.0
    return torch.tensor([v, v**0.5, v**2.0], dtype=torch.float32)


def k_feat(k: int, k_max: int = 90) -> torch.Tensor:
    kn = (k - 1) / (k_max - 1)
    return torch.tensor([kn, math.log(k)/math.log(k_max), 1.0/k], dtype=torch.float32)


def freq_feat(freq_hz: float, sr: int = 44100) -> torch.Tensor:
    fn = math.log(max(freq_hz, 10.0)) / math.log(sr/2)
    return torch.tensor([fn, fn**2], dtype=torch.float32)


def mlp(in_dim: int, hidden: int, out_dim: int, layers: int = 3) -> nn.Sequential:
    dims = [in_dim] + [hidden]*layers + [out_dim]
    mods = []
    for i in range(len(dims) - 1):
        mods.append(nn.Linear(dims[i], dims[i+1]))
        if i < len(dims) - 2:
            mods.append(nn.SiLU())
    return nn.Sequential(*mods)


# ─────────────────────────────────────────────────────────────────────────────
# InstrumentProfile network
# ─────────────────────────────────────────────────────────────────────────────

class InstrumentProfile(nn.Module):
    """
    Factorised NN: separate sub-nets for vel-independent and vel-dependent params.

    B_net        MLP(midi)           → log(B)
    dur_net      MLP(midi)           → log(dur)
    tau1_k1_net  MLP(midi, vel)      → log(tau1) for k=1
    tau_ratio_net MLP(midi, k)       → log(tau_k / tau_k1)
    A0_net       MLP(midi, k, vel)   → log(A0_ratio)
    df_net       MLP(midi, k)        → log(beat_hz)
    eq_net       MLP(midi, freq)     → gain_db
    wf_net       MLP(midi)           → log(stereo_width_factor)
    noise_net    MLP(midi, vel)      → [log(attack_tau), log(centroid), log(A_noise)]
    biexp_net    MLP(midi, k, vel)   → [logit(a1), log(tau2/tau1)]
    phi_net      MLP(midi, vel)      → phi_diff  (relative string phase)
    """

    def __init__(self, hidden: int = 64):
        super().__init__()
        self.B_net         = mlp(MIDI_DIM, hidden, 1)
        self.dur_net       = mlp(MIDI_DIM, hidden, 1)
        self.tau1_k1_net   = mlp(MIDI_DIM + VEL_DIM, hidden, 1)
        self.tau_ratio_net = mlp(MIDI_DIM + K_DIM, hidden, 1)
        self.A0_net        = mlp(MIDI_DIM + K_DIM + VEL_DIM, hidden, 1)
        self.df_net        = mlp(MIDI_DIM + K_DIM, hidden, 1)
        self.eq_net        = mlp(MIDI_DIM + FREQ_DIM, hidden, 1)
        self.wf_net        = mlp(MIDI_DIM, hidden, 1)
        self.noise_net     = mlp(MIDI_DIM + VEL_DIM, hidden, 3)
        self.biexp_net     = mlp(MIDI_DIM + K_DIM + VEL_DIM, hidden, 2)
        self.phi_net       = mlp(MIDI_DIM + VEL_DIM, hidden, 1)

        # Physically motivated initial biases
        nn.init.constant_(self.B_net[-1].bias, -9.2)          # B ≈ 1e-4
        nn.init.constant_(self.noise_net[-1].bias[0], -3.0)   # attack_tau ≈ 0.05s
        nn.init.constant_(self.noise_net[-1].bias[1],  8.0)   # centroid ≈ 3000 Hz
        nn.init.constant_(self.noise_net[-1].bias[2], -2.8)   # A_noise ≈ 0.06
        nn.init.constant_(self.biexp_net[-1].bias[0],  1.73)  # a1 ≈ 0.85
        nn.init.constant_(self.biexp_net[-1].bias[1],  1.10)  # tau2 ≈ 3×tau1
        nn.init.constant_(self.phi_net[-1].bias, 0.0)

    def forward_B(self, mf, vf=None):               return self.B_net(mf)
    def forward_dur(self, mf, vf=None):             return self.dur_net(mf)
    def forward_tau1_k1(self, mf, vf):              return self.tau1_k1_net(torch.cat([mf, vf], -1))
    def forward_tau_ratio(self, mf, kf, vf=None):   return self.tau_ratio_net(torch.cat([mf, kf], -1))
    def forward_A0(self, mf, kf, vf):               return self.A0_net(torch.cat([mf, kf, vf], -1))
    def forward_df(self, mf, kf, vf=None):          return self.df_net(torch.cat([mf, kf], -1))
    def forward_eq(self, mf, ff, vf=None):          return self.eq_net(torch.cat([mf, ff], -1))
    def forward_wf(self, mf, vf=None):              return self.wf_net(mf)
    def forward_noise(self, mf, vf):                return self.noise_net(torch.cat([mf, vf], -1))
    def forward_biexp(self, mf, kf, vf):            return self.biexp_net(torch.cat([mf, kf, vf], -1))
    def forward_phi(self, mf, vf):                  return self.phi_net(torch.cat([mf, vf], -1))


# ─────────────────────────────────────────────────────────────────────────────
# ProfileTrainer
# ─────────────────────────────────────────────────────────────────────────────

class ProfileTrainer:
    """
    Train an InstrumentProfile NN from a params dict.

    Usage:
        trainer = ProfileTrainer()

        # Train from scratch
        model = trainer.train(params, epochs=1800)

        # Load a previously saved model
        model = trainer.load("training/profile-ks-grand.pt")

        # Generate predictions for all 88×8 notes
        pred_params = trainer.predict_all(model)
    """

    def train(
        self,
        params:   dict,
        epochs:   int   = 1800,
        hidden:   int   = 64,
        lr:       float = 0.003,
        val_frac: float = 0.15,
        verbose:  bool  = True,
    ) -> InstrumentProfile:
        """
        Train an InstrumentProfile from extracted physics params.

        Args:
            params:   Params dict (keys: samples, …) from ParamExtractor.
            epochs:   Training epochs.
            hidden:   MLP hidden layer width.
            lr:       Learning rate (Adam + cosine LR schedule).
            val_frac: Fraction of MIDI notes held out for validation
                      (every N-th MIDI, deterministic). Default 0.15.
            verbose:  Print loss every 100 epochs.

        Returns:
            Trained InstrumentProfile (best val-loss checkpoint, eval mode).
        """
        samples  = params["notes"]
        measured = {k: v for k, v in samples.items()
                    if not v.get("is_interpolated")}

        train_s, val_s = _split_val_midis(measured, val_frac)
        val_midis = sorted({s["midi"] for s in val_s.values()})
        print(f"ProfileTrainer: {len(measured)} measured samples  "
              f"→  train={len(train_s)}  val={len(val_s)} "
              f"(MIDI {val_midis[0]}–{val_midis[-1]}, every ~{len(measured)//max(len(val_s),1)}th)")

        print("Building datasets …")
        train_ds = build_dataset(train_s)
        val_ds   = build_dataset(val_s)
        model    = InstrumentProfile(hidden=hidden)

        n_p = sum(p.numel() for p in model.parameters())
        print(f"Model parameters: {n_p:,}")
        print(f"Training {epochs} epochs …")

        _run_training(model, train_ds, val_ds=val_ds,
                      epochs=epochs, lr=lr, verbose=verbose)
        model.eval()
        return model

    def load(self, path: str) -> InstrumentProfile:
        """
        Load a previously saved InstrumentProfile from a .pt checkpoint.

        Checkpoint format: {"state_dict": …, "hidden": int, "eq_freqs": list|None}
        """
        ckpt   = torch.load(path, map_location="cpu", weights_only=False)
        hidden = ckpt.get("hidden", 64)
        model  = InstrumentProfile(hidden=hidden)

        missing, unexpected = model.load_state_dict(ckpt["state_dict"], strict=False)
        if missing:
            print(f"  [load] new params (fresh init): {missing}")
        if unexpected:
            print(f"  [load] unexpected keys (ignored): {unexpected}")

        model.eval()
        return model

    def predict_all(
        self,
        model:     InstrumentProfile,
        midi_from: int = 21,
        midi_to:   int = 108,
        sr:        int = 44_100,
    ) -> dict:
        """
        Run inference for all 88×8 (midi, vel) positions.

        Returns a params-format dict with key "notes" containing NN-generated
        entries for every note in [midi_from, midi_to] at all 8 velocity layers.
        """
        ds      = {"batches": {}, "eq_freqs": None}
        samples = generate_profile(
            model, ds,
            midi_from=midi_from, midi_to=midi_to,
            sr=sr, orig_samples=None,
        )
        return {"notes": samples}

    def save(self, model: InstrumentProfile, path: str,
             eq_freqs=None, hidden: int = 64) -> None:
        """Save an InstrumentProfile checkpoint."""
        eq_list = eq_freqs.tolist() if hasattr(eq_freqs, "tolist") else eq_freqs
        torch.save({
            "state_dict": model.state_dict(),
            "hidden":     hidden,
            "eq_freqs":   eq_list,
        }, path)
        print(f"Saved model → {path}")


# ─────────────────────────────────────────────────────────────────────────────
# Dataset construction (from train_instrument_profile.build_dataset)
# ─────────────────────────────────────────────────────────────────────────────

def _split_val_midis(samples: dict, val_frac: float) -> tuple:
    """
    Split samples into (train, val) by holding out every N-th MIDI note.

    Deterministic: val notes are evenly spaced across the MIDI range,
    starting at step//2 to avoid always picking edge notes.
    """
    midis = sorted({s["midi"] for s in samples.values()})
    n_val = max(1, round(len(midis) * val_frac))
    step  = max(1, len(midis) // n_val)
    val_set = set(midis[i] for i in range(step // 2, len(midis), step))
    train = {k: v for k, v in samples.items() if v["midi"] not in val_set}
    val   = {k: v for k, v in samples.items() if v["midi"] in val_set}
    return train, val


def build_dataset(samples: dict) -> dict:
    """Extract training tensors from raw params dict."""
    B_data, dur_data, wf_data = [], [], []
    tau_data, tau1_k1_data, A0_data, df_data, eq_data = [], [], [], [], []
    noise_data, biexp_data = [], []

    eq_freqs = None
    for s in samples.values():
        eq = s.get("spectral_eq") or {}
        if eq.get("freqs_hz"):
            eq_freqs = np.array(eq["freqs_hz"])
            break

    for key, s in samples.items():
        if s.get("is_interpolated"):
            continue
        midi = s.get("midi"); vel = s.get("vel")
        if midi is None or vel is None:
            continue

        mf = midi_feat(midi); vf = vel_feat(vel)

        B = s.get("B") or 0
        if B > 1e-7:
            B_data.append((mf, math.log(B)))

        dur = s.get("duration_s") or 0
        if dur > 0.1:
            dur_data.append((mf, math.log(dur)))

        eq = s.get("spectral_eq") or {}
        wf = eq.get("stereo_width_factor") or 0
        if wf > 0.1:
            wf_data.append((mf, math.log(wf)))

        if eq_freqs is not None and eq.get("gains_db"):
            gd = np.array(eq["gains_db"])
            if len(gd) == len(eq_freqs):
                for fhz, g in zip(eq_freqs, gd):
                    eq_data.append((mf, freq_feat(float(fhz)), float(g)))

        atk_tau  = s.get("attack_tau") or 0
        centroid = s.get("noise_centroid_hz") or 0
        A_noise  = s.get("A_noise") or 0
        if atk_tau > 0.001 and centroid > 50 and A_noise > 0.001:
            noise_data.append((mf, vf,
                                math.log(max(atk_tau, 1e-4)),
                                math.log(max(centroid, 10.0)),
                                math.log(max(A_noise, 1e-4))))

        parts     = {p["k"]: p for p in s.get("partials", []) if "k" in p}
        a0_k1     = parts.get(1, {}).get("A0") or 0
        tau1_k1_v = parts.get(1, {}).get("tau1") or 0

        for k, p in parts.items():
            kf = k_feat(k)
            t1 = p.get("tau1") or 0

            if k == 1 and t1 > 0.005:
                tau1_k1_data.append((mf, vf, math.log(t1)))

            if 2 <= k <= 10 and t1 > 0.005 and tau1_k1_v > 0.005:
                ratio = t1 / tau1_k1_v
                if 1e-4 < ratio < 100:
                    tau_data.append((mf, kf, min(0.5, math.log(ratio))))

            a0 = p.get("A0") or 0
            if a0 > 0 and a0_k1 > 0:
                ratio = a0 / a0_k1
                if 0.01 < ratio < 20.0:
                    A0_data.append((mf, kf, vf, math.log(ratio)))

            df = p.get("beat_hz") or p.get("df") or 0
            if df > 0.001:
                df_data.append((mf, kf, math.log(df)))

            a1_val = p.get("a1"); tau2_val = p.get("tau2")
            if (a1_val is not None and tau2_val is not None
                    and 0.01 < a1_val < 0.99 and t1 > 0.005
                    and tau2_val > t1*3.0):
                biexp_data.append((mf, kf, vf,
                                   math.log(a1_val/(1.0-a1_val)),
                                   math.log(tau2_val/t1)))

    # IQR outlier filtering before batching
    def iqr_filter(items, val_idx, k_iqr=3.0):
        vals = np.array([x[val_idx] for x in items], dtype=float)
        if len(vals) < 4: return items
        q25, q75 = np.percentile(vals, 25), np.percentile(vals, 75)
        iqr = q75 - q25
        if iqr < 1e-12: return items
        med = np.median(vals)
        return [x for x, v in zip(items, vals) if abs(v-med) <= k_iqr*iqr]

    B_data        = iqr_filter(B_data,        1)
    dur_data      = iqr_filter(dur_data,       1)
    wf_data       = iqr_filter(wf_data,        1)
    tau_data      = iqr_filter(tau_data,       2, 1.5)
    tau1_k1_data  = iqr_filter(tau1_k1_data,   2)
    A0_data       = iqr_filter(A0_data,        3, 2.0)
    df_data       = iqr_filter(df_data,        2)
    noise_data    = iqr_filter(noise_data,     2)
    biexp_data    = iqr_filter(biexp_data,     3)

    b = {}

    if B_data:
        b["B_mf"] = torch.stack([d[0] for d in B_data])
        b["B_y"]  = torch.tensor([d[1] for d in B_data], dtype=torch.float32)
    if dur_data:
        b["dur_mf"] = torch.stack([d[0] for d in dur_data])
        b["dur_y"]  = torch.tensor([d[1] for d in dur_data], dtype=torch.float32)
    if wf_data:
        b["wf_mf"] = torch.stack([d[0] for d in wf_data])
        b["wf_y"]  = torch.tensor([d[1] for d in wf_data], dtype=torch.float32)
    if tau1_k1_data:
        b["tk1_mf"] = torch.stack([d[0] for d in tau1_k1_data])
        b["tk1_vf"] = torch.stack([d[1] for d in tau1_k1_data])
        b["tk1_y"]  = torch.tensor([d[2] for d in tau1_k1_data], dtype=torch.float32)
    if tau_data:
        b["tau_mf"] = torch.stack([d[0] for d in tau_data])
        b["tau_kf"] = torch.stack([d[1] for d in tau_data])
        b["tau_y"]  = torch.tensor([d[2] for d in tau_data], dtype=torch.float32)
        b["tau_w"]  = torch.tensor(
            [1.0/(1.0+float(d[1][0])*2) for d in tau_data], dtype=torch.float32)
    if A0_data:
        b["a0_mf"] = torch.stack([d[0] for d in A0_data])
        b["a0_kf"] = torch.stack([d[1] for d in A0_data])
        b["a0_vf"] = torch.stack([d[2] for d in A0_data])
        b["a0_y"]  = torch.tensor([d[3] for d in A0_data], dtype=torch.float32)
    if df_data:
        b["df_mf"] = torch.stack([d[0] for d in df_data])
        b["df_kf"] = torch.stack([d[1] for d in df_data])
        b["df_y"]  = torch.tensor([d[2] for d in df_data], dtype=torch.float32)

    eq_sub = eq_data[::4] if len(eq_data) > 400 else eq_data
    if eq_sub:
        b["eq_mf"] = torch.stack([d[0] for d in eq_sub])
        b["eq_ff"] = torch.stack([d[1] for d in eq_sub])
        b["eq_y"]  = torch.tensor([d[2] for d in eq_sub], dtype=torch.float32)
    if noise_data:
        b["noise_mf"] = torch.stack([d[0] for d in noise_data])
        b["noise_vf"] = torch.stack([d[1] for d in noise_data])
        b["noise_y"]  = torch.tensor(
            [[d[2],d[3],d[4]] for d in noise_data], dtype=torch.float32)
    if biexp_data:
        b["biexp_mf"] = torch.stack([d[0] for d in biexp_data])
        b["biexp_kf"] = torch.stack([d[1] for d in biexp_data])
        b["biexp_vf"] = torch.stack([d[2] for d in biexp_data])
        b["biexp_y"]  = torch.tensor(
            [[d[3],d[4]] for d in biexp_data], dtype=torch.float32)

    return dict(
        batches=b, eq_freqs=eq_freqs,
        n_B=len(B_data), n_tau=len(tau_data), n_tau1_k1=len(tau1_k1_data),
        n_A0=len(A0_data), n_df=len(df_data),
        n_eq=len(eq_sub) if eq_sub else 0,
        n_noise=len(noise_data), n_biexp=len(biexp_data),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Training loop
# ─────────────────────────────────────────────────────────────────────────────

def _compute_data_loss(model: InstrumentProfile, b: dict) -> torch.Tensor:
    """Data-fit loss terms only (no smoothness penalty). Used for train and val."""
    terms = []

    if "B_mf"   in b:
        terms.append(nn.functional.mse_loss(
            model.forward_B(b["B_mf"]).squeeze(-1), b["B_y"]))

    if "dur_mf" in b:
        pred  = model.forward_dur(b["dur_mf"]).squeeze(-1)
        dur_w = torch.exp(b["dur_y"]*0.1); dur_w /= dur_w.mean()
        terms.append((dur_w*(pred-b["dur_y"])**2).mean())

    if "wf_mf"  in b:
        terms.append(nn.functional.mse_loss(
            model.forward_wf(b["wf_mf"]).squeeze(-1), b["wf_y"]))

    if "tk1_mf" in b:
        pred = model.forward_tau1_k1(b["tk1_mf"], b["tk1_vf"]).squeeze(-1)
        terms.append(2.0 * nn.functional.mse_loss(pred, b["tk1_y"]))

    if "tau_mf" in b:
        pred = model.forward_tau_ratio(b["tau_mf"], b["tau_kf"]).squeeze(-1)
        terms.append((b["tau_w"] * nn.functional.huber_loss(
            pred, b["tau_y"], delta=0.3, reduction="none")).mean())

    if "a0_mf"  in b:
        terms.append(nn.functional.mse_loss(
            model.forward_A0(b["a0_mf"], b["a0_kf"], b["a0_vf"]).squeeze(-1),
            b["a0_y"]))

    if "df_mf"  in b:
        terms.append(nn.functional.mse_loss(
            model.forward_df(b["df_mf"], b["df_kf"]).squeeze(-1), b["df_y"]))

    if "eq_mf"  in b:
        terms.append(0.1 * nn.functional.mse_loss(
            model.forward_eq(b["eq_mf"], b["eq_ff"]).squeeze(-1), b["eq_y"]))

    if "noise_mf" in b:
        terms.append(nn.functional.mse_loss(
            model.forward_noise(b["noise_mf"], b["noise_vf"]), b["noise_y"]))

    if "biexp_mf" in b:
        terms.append(nn.functional.mse_loss(
            model.forward_biexp(b["biexp_mf"], b["biexp_kf"], b["biexp_vf"]),
            b["biexp_y"]))

    return sum(terms)/len(terms) if terms else torch.tensor(0.0)


def _run_training(
    model:      InstrumentProfile,
    ds:         dict,
    val_ds:     dict  = None,
    epochs:     int   = 800,
    lr:         float = 3e-3,
    eval_every: int   = 100,
    verbose:    bool  = True,
) -> list:
    opt   = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs, eta_min=lr*0.01)
    b     = ds["batches"]
    losses = []

    best_val   = float("inf")
    best_state = copy.deepcopy(model.state_dict())

    for epoch in range(1, epochs+1):
        model.train()
        opt.zero_grad()

        loss = _compute_data_loss(model, b)

        # Smoothness penalty on a fixed MIDI grid (every 5 epochs)
        if epoch % 5 == 0:
            midi_grid = torch.arange(21, 108, dtype=torch.float32)
            mf_grid   = torch.stack([midi_feat(float(m)) for m in midi_grid])
            kf_ref    = k_feat(1).unsqueeze(0).expand(len(midi_grid), -1)
            vf_ref    = vel_feat(4).unsqueeze(0).expand(len(midi_grid), -1)
            B_g   = model.forward_B(mf_grid).squeeze(-1)
            tau_g = model.forward_tau1_k1(mf_grid, vf_ref).squeeze(-1)
            a0_g  = model.forward_A0(mf_grid, kf_ref, vf_ref).squeeze(-1)
            n_g   = model.forward_noise(mf_grid, vf_ref)
            smooth = ((B_g[1:]-B_g[:-1]).pow(2).mean()
                      + (tau_g[1:]-tau_g[:-1]).pow(2).mean()
                      + (a0_g[1:]-a0_g[:-1]).pow(2).mean()
                      + (n_g[1:]-n_g[:-1]).pow(2).mean())
            loss = loss + 0.3*smooth

        loss.backward(); opt.step(); sched.step()
        losses.append(float(loss.detach()))

        # Validation
        if val_ds and (epoch % eval_every == 0 or epoch == epochs):
            model.eval()
            with torch.no_grad():
                val_loss = _compute_data_loss(model, val_ds["batches"]).item()
            if val_loss < best_val:
                best_val   = val_loss
                best_state = copy.deepcopy(model.state_dict())
                improved   = " ✓"
            else:
                improved   = ""
            if verbose:
                print(f"  epoch {epoch:4d}/{epochs}  "
                      f"train={loss.item():.4f}  val={val_loss:.4f}"
                      f"  lr={sched.get_last_lr()[0]:.2e}{improved}")
        elif verbose and epoch % eval_every == 0:
            print(f"  epoch {epoch:4d}/{epochs}  loss={loss.item():.6f}  "
                  f"lr={sched.get_last_lr()[0]:.2e}")

    if val_ds:
        model.load_state_dict(best_state)
        print(f"  Restored best checkpoint (val={best_val:.4f})")

    return losses


# ─────────────────────────────────────────────────────────────────────────────
# Profile generation (inference across the full 88×8 keyboard)
# ─────────────────────────────────────────────────────────────────────────────

def generate_profile(
    model:        InstrumentProfile,
    ds:           dict,
    midi_from:    int  = 21,
    midi_to:      int  = 108,
    sr:           int  = 44_100,
    orig_samples: dict = None,
) -> dict:
    """
    Evaluate trained model at all (midi, vel) positions.
    Returns a samples dict compatible with params.json format.

    If orig_samples is provided, measured entries are preserved verbatim.
    """
    model.eval()
    samples_out = {}
    eq_freqs    = ds.get("eq_freqs")

    with torch.no_grad():
        for midi in range(midi_from, midi_to+1):
            mf = midi_feat(midi)
            f0 = midi_to_hz(midi)

            B   = float(np.clip(float(torch.exp(model.forward_B(mf)).item()),   1e-8, None))
            dur = float(np.clip(float(torch.exp(model.forward_dur(mf)).item()), 0.3, None))
            wf  = float(np.clip(float(torch.exp(model.forward_wf(mf)).item()), 0.1, 10.0))

            spectral_eq: dict = {}
            if eq_freqs is not None:
                gains = [float(np.clip(float(model.forward_eq(mf, freq_feat(float(fhz))).item()), -30, 20))
                         for fhz in eq_freqs]
                spectral_eq = {
                    "freqs_hz":           [round(float(f),2)  for f in eq_freqs],
                    "gains_db":           [round(g, 4) for g in gains],
                    "stereo_width_factor": round(wf, 4),
                }

            n_partials = max(1, int((sr/2)/f0))

            for vel in range(8):
                vf  = vel_feat(vel)
                key = f"m{midi:03d}_vel{vel}"

                noise_pred = model.forward_noise(mf, vf).squeeze(0)
                noise_out = {
                    "attack_tau":        round(float(np.clip(float(torch.exp(noise_pred[0]).item()), 0.002, 1.0)), 5),
                    "noise_centroid_hz": round(float(np.clip(float(torch.exp(noise_pred[1]).item()), 100.0, 20000.0)), 1),
                    "A_noise":           round(float(np.clip(float(torch.exp(noise_pred[2]).item()), 0.001, 0.5)), 5),
                }

                partials = []
                for k in range(1, n_partials+1):
                    kf  = k_feat(k)
                    f_k = k*f0*math.sqrt(1.0 + B*k**2)
                    if f_k >= sr/2: break

                    tau1_k1 = float(torch.exp(model.forward_tau1_k1(mf, vf)).item())
                    if k == 1:
                        tau1 = tau1_k1
                    else:
                        log_ratio  = float(model.forward_tau_ratio(mf, kf).item())
                        log_k_bias = -0.3*math.log(k)
                        log_ratio  = max(log_k_bias-2.0, min(0.0, log_ratio))
                        tau1 = tau1_k1*math.exp(log_ratio)
                    tau1 = max(tau1, 0.005)

                    biexp    = model.forward_biexp(mf, kf, vf).squeeze(0)
                    a1_val   = float(np.clip(float(torch.sigmoid(biexp[0]).item()), 0.05, 0.99))
                    tau2_val = tau1*max(float(torch.exp(biexp[1]).item()), 3.0)
                    emit_biexp = a1_val < 0.92

                    a0_ratio = float(np.clip(float(torch.exp(model.forward_A0(mf, kf, vf)).item()), 1e-6, None))
                    df       = float(np.clip(float(torch.exp(model.forward_df(mf, kf)).item()), 0.0, None))

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
                    "f0_hz": round(f0, 6),
                    "B": round(float(B), 8), "duration_s": round(float(dur), 3),
                    "partials": partials, **noise_out,
                    "is_interpolated": True,
                }
                if spectral_eq:
                    sample["spectral_eq"] = spectral_eq

                # Preserve measured data where available
                if orig_samples and key in orig_samples and not orig_samples[key].get("is_interpolated"):
                    sample = copy.deepcopy(orig_samples[key])
                    sample["is_interpolated"] = False

                samples_out[key] = sample

    return samples_out
