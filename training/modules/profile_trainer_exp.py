"""
training/modules/profile_trainer_exp.py
─────────────────────────────────────────
Experimental InstrumentProfile: all sub-networks take velocity as input.

Difference from standard ProfileTrainer (used in full pipeline):
    Standard — 6 sub-networks are velocity-independent:
        B_net, dur_net, tau_ratio_net, df_net, eq_net, wf_net
    Experimental — every sub-network receives (midi, vel [, k, freq]),
        allowing velocity to influence inharmonicity, decay ratios,
        beating frequency and body EQ.

Public API:
    model = ProfileTrainerExp().train(params, epochs=3000)
    model = ProfileTrainerExp().load("profile-exp.pt")
"""

import copy
import math

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

from training.modules.profile_trainer import (
    InstrumentProfile, ProfileTrainer,
    MIDI_DIM, VEL_DIM, K_DIM, FREQ_DIM,
    midi_feat, vel_feat, k_feat, freq_feat, midi_to_hz,
    mlp, _split_val_midis,
)


# ─────────────────────────────────────────────────────────────────────────────
# InstrumentProfileExp — velocity added to all 10 sub-networks
# ─────────────────────────────────────────────────────────────────────────────

class InstrumentProfileExp(InstrumentProfile):
    """
    All 10 sub-networks receive velocity as input.

    Changed vs InstrumentProfile (vel added):
        B_net         MLP(midi, vel)           → log(B)
        dur_net       MLP(midi, vel)           → log(dur)
        tau_ratio_net MLP(midi, k, vel)        → log(tau_k / tau_k1)
        df_net        MLP(midi, k, vel)        → log(beat_hz)
        eq_net        MLP(midi, freq, vel)     → gain_db
        wf_net        MLP(midi, vel)           → log(stereo_width_factor)

    Unchanged (already had velocity):
        tau1_k1_net, A0_net, noise_net, biexp_net, phi_net
    """

    def __init__(self, hidden: int = 64):
        nn.Module.__init__(self)           # skip InstrumentProfile.__init__

        # ── 6 networks extended with velocity ────────────────────────────────
        self.B_net         = mlp(MIDI_DIM + VEL_DIM, hidden, 1)
        self.dur_net       = mlp(MIDI_DIM + VEL_DIM, hidden, 1)
        self.tau_ratio_net = mlp(MIDI_DIM + K_DIM + VEL_DIM, hidden, 1)
        self.df_net        = mlp(MIDI_DIM + K_DIM + VEL_DIM, hidden, 1)
        self.eq_net        = mlp(MIDI_DIM + FREQ_DIM + VEL_DIM, hidden, 1)
        self.wf_net        = mlp(MIDI_DIM + VEL_DIM, hidden, 1)

        # ── 5 networks unchanged (already had velocity) ───────────────────────
        self.tau1_k1_net   = mlp(MIDI_DIM + VEL_DIM, hidden, 1)
        self.A0_net        = mlp(MIDI_DIM + K_DIM + VEL_DIM, hidden, 1)
        self.noise_net     = mlp(MIDI_DIM + VEL_DIM, hidden, 3)
        self.biexp_net     = mlp(MIDI_DIM + K_DIM + VEL_DIM, hidden, 2)
        self.phi_net       = mlp(MIDI_DIM + VEL_DIM, hidden, 1)

        # Physically motivated initial biases (same as standard)
        nn.init.constant_(self.B_net[-1].bias,         -9.2)
        nn.init.constant_(self.noise_net[-1].bias[0],  -3.0)
        nn.init.constant_(self.noise_net[-1].bias[1],   8.0)
        nn.init.constant_(self.noise_net[-1].bias[2],  -2.8)
        nn.init.constant_(self.biexp_net[-1].bias[0],   1.73)
        nn.init.constant_(self.biexp_net[-1].bias[1],   1.10)
        nn.init.constant_(self.phi_net[-1].bias,        0.0)

    # ── Overridden forward methods (vel added) ────────────────────────────────

    def forward_B(self, mf, vf):
        return self.B_net(torch.cat([mf, vf], -1))

    def forward_dur(self, mf, vf):
        return self.dur_net(torch.cat([mf, vf], -1))

    def forward_tau_ratio(self, mf, kf, vf):
        return self.tau_ratio_net(torch.cat([mf, kf, vf], -1))

    def forward_df(self, mf, kf, vf):
        return self.df_net(torch.cat([mf, kf, vf], -1))

    def forward_eq(self, mf, ff, vf):
        return self.eq_net(torch.cat([mf, ff, vf], -1))

    def forward_wf(self, mf, vf):
        return self.wf_net(torch.cat([mf, vf], -1))


# ─────────────────────────────────────────────────────────────────────────────
# Dataset builder (vel added to formerly vel-independent fields)
# ─────────────────────────────────────────────────────────────────────────────

def build_dataset_exp(samples: dict) -> dict:
    """
    Like build_dataset but includes velocity features for all fields.

    Changed tensor keys vs standard build_dataset:
        B:   B_mf, B_vf, B_y
        dur: dur_mf, dur_vf, dur_y
        wf:  wf_mf, wf_vf, wf_y
        tau: tau_mf, tau_kf, tau_vf, tau_y, tau_w
        df:  df_mf, df_kf, df_vf, df_y
        eq:  eq_mf, eq_ff, eq_vf, eq_y
    """
    B_data, dur_data, wf_data = [], [], []
    tau_data, tau1_k1_data, A0_data, df_data, eq_data = [], [], [], [], []
    noise_data, biexp_data = [], []

    eq_freqs = None
    for s in samples.values():
        eq = s.get("spectral_eq") or {}
        if eq.get("freqs_hz"):
            eq_freqs = np.array(eq["freqs_hz"])
            break

    for s in samples.values():
        if s.get("_interpolated"):
            continue
        midi = s.get("midi"); vel = s.get("vel")
        if midi is None or vel is None:
            continue

        mf = midi_feat(midi)
        vf = vel_feat(vel)

        B = s.get("B") or 0
        if B > 1e-7:
            B_data.append((mf, vf, math.log(B)))

        dur = s.get("duration_s") or 0
        if dur > 0.1:
            dur_data.append((mf, vf, math.log(dur)))

        eq = s.get("spectral_eq") or {}
        wf = eq.get("stereo_width_factor") or 0
        if wf > 0.1:
            wf_data.append((mf, vf, math.log(wf)))

        if eq_freqs is not None and eq.get("gains_db"):
            gd = np.array(eq["gains_db"])
            if len(gd) == len(eq_freqs):
                for fhz, g in zip(eq_freqs, gd):
                    eq_data.append((mf, freq_feat(float(fhz)), vf, float(g)))

        noise    = s.get("noise") or {}
        atk_tau  = noise.get("attack_tau") or 0
        centroid = noise.get("centroid_hz") or 0
        A_noise  = noise.get("A_noise") or noise.get("floor_rms") or 0
        if atk_tau > 0.001 and centroid > 50 and A_noise > 0.001:
            noise_data.append((mf, vf,
                                math.log(max(atk_tau, 1e-4)),
                                math.log(max(centroid, 10.0)),
                                math.log(max(A_noise, 1e-4))))

        parts       = {p["k"]: p for p in s.get("partials", []) if "k" in p}
        a0_k1       = parts.get(1, {}).get("A0") or 0
        tau1_k1_v   = parts.get(1, {}).get("tau1") or 0

        for k, p in parts.items():
            kf = k_feat(k)
            t1 = p.get("tau1") or 0

            if k == 1 and t1 > 0.005:
                tau1_k1_data.append((mf, vf, math.log(t1)))

            if 2 <= k <= 10 and t1 > 0.005 and tau1_k1_v > 0.005:
                ratio = t1 / tau1_k1_v
                if 1e-4 < ratio < 100:
                    tau_data.append((mf, kf, vf, min(0.5, math.log(ratio))))

            a0 = p.get("A0") or 0
            if a0 > 0 and a0_k1 > 0:
                ratio = a0 / a0_k1
                if 0.01 < ratio < 20.0:
                    A0_data.append((mf, kf, vf, math.log(ratio)))

            df = p.get("beat_hz") or p.get("df") or 0
            if df > 0.001:
                df_data.append((mf, kf, vf, math.log(df)))

            a1_val = p.get("a1"); tau2_val = p.get("tau2")
            if (a1_val is not None and tau2_val is not None
                    and 0.01 < a1_val < 0.99 and t1 > 0.005
                    and tau2_val > t1*3.0):
                biexp_data.append((mf, kf, vf,
                                   math.log(a1_val/(1.0-a1_val)),
                                   math.log(tau2_val/t1)))

    # IQR outlier filtering (val index shifted by 1 for vel-extended fields)
    def iqr_filter(items, val_idx, k_iqr=3.0):
        vals = np.array([x[val_idx] for x in items], dtype=float)
        if len(vals) < 4: return items
        q25, q75 = np.percentile(vals, 25), np.percentile(vals, 75)
        iqr = q75 - q25
        if iqr < 1e-12: return items
        med = np.median(vals)
        return [x for x, v in zip(items, vals) if abs(v - med) <= k_iqr * iqr]

    B_data        = iqr_filter(B_data,        2)        # val at idx 2 (was 1)
    dur_data      = iqr_filter(dur_data,       2)
    wf_data       = iqr_filter(wf_data,        2)
    tau_data      = iqr_filter(tau_data,       3, 1.5)  # val at idx 3 (was 2)
    tau1_k1_data  = iqr_filter(tau1_k1_data,   2)       # unchanged
    A0_data       = iqr_filter(A0_data,        3, 2.0)  # unchanged
    df_data       = iqr_filter(df_data,        3)       # val at idx 3 (was 2)
    noise_data    = iqr_filter(noise_data,     2)       # unchanged
    biexp_data    = iqr_filter(biexp_data,     3)       # unchanged

    b = {}

    if B_data:
        b["B_mf"] = torch.stack([d[0] for d in B_data])
        b["B_vf"] = torch.stack([d[1] for d in B_data])
        b["B_y"]  = torch.tensor([d[2] for d in B_data], dtype=torch.float32)
    if dur_data:
        b["dur_mf"] = torch.stack([d[0] for d in dur_data])
        b["dur_vf"] = torch.stack([d[1] for d in dur_data])
        b["dur_y"]  = torch.tensor([d[2] for d in dur_data], dtype=torch.float32)
    if wf_data:
        b["wf_mf"] = torch.stack([d[0] for d in wf_data])
        b["wf_vf"] = torch.stack([d[1] for d in wf_data])
        b["wf_y"]  = torch.tensor([d[2] for d in wf_data], dtype=torch.float32)
    if tau1_k1_data:
        b["tk1_mf"] = torch.stack([d[0] for d in tau1_k1_data])
        b["tk1_vf"] = torch.stack([d[1] for d in tau1_k1_data])
        b["tk1_y"]  = torch.tensor([d[2] for d in tau1_k1_data], dtype=torch.float32)
    if tau_data:
        b["tau_mf"] = torch.stack([d[0] for d in tau_data])
        b["tau_kf"] = torch.stack([d[1] for d in tau_data])
        b["tau_vf"] = torch.stack([d[2] for d in tau_data])
        b["tau_y"]  = torch.tensor([d[3] for d in tau_data], dtype=torch.float32)
        b["tau_w"]  = torch.tensor(
            [1.0/(1.0 + float(d[1][0])*2) for d in tau_data], dtype=torch.float32)
    if A0_data:
        b["a0_mf"] = torch.stack([d[0] for d in A0_data])
        b["a0_kf"] = torch.stack([d[1] for d in A0_data])
        b["a0_vf"] = torch.stack([d[2] for d in A0_data])
        b["a0_y"]  = torch.tensor([d[3] for d in A0_data], dtype=torch.float32)
    if df_data:
        b["df_mf"] = torch.stack([d[0] for d in df_data])
        b["df_kf"] = torch.stack([d[1] for d in df_data])
        b["df_vf"] = torch.stack([d[2] for d in df_data])
        b["df_y"]  = torch.tensor([d[3] for d in df_data], dtype=torch.float32)

    eq_sub = eq_data[::4] if len(eq_data) > 400 else eq_data
    if eq_sub:
        b["eq_mf"] = torch.stack([d[0] for d in eq_sub])
        b["eq_ff"] = torch.stack([d[1] for d in eq_sub])
        b["eq_vf"] = torch.stack([d[2] for d in eq_sub])
        b["eq_y"]  = torch.tensor([d[3] for d in eq_sub], dtype=torch.float32)
    if noise_data:
        b["noise_mf"] = torch.stack([d[0] for d in noise_data])
        b["noise_vf"] = torch.stack([d[1] for d in noise_data])
        b["noise_y"]  = torch.tensor(
            [[d[2], d[3], d[4]] for d in noise_data], dtype=torch.float32)
    if biexp_data:
        b["biexp_mf"] = torch.stack([d[0] for d in biexp_data])
        b["biexp_kf"] = torch.stack([d[1] for d in biexp_data])
        b["biexp_vf"] = torch.stack([d[2] for d in biexp_data])
        b["biexp_y"]  = torch.tensor(
            [[d[3], d[4]] for d in biexp_data], dtype=torch.float32)

    return dict(
        batches=b, eq_freqs=eq_freqs,
        n_B=len(B_data), n_tau=len(tau_data), n_tau1_k1=len(tau1_k1_data),
        n_A0=len(A0_data), n_df=len(df_data),
        n_eq=len(eq_sub) if eq_sub else 0,
        n_noise=len(noise_data), n_biexp=len(biexp_data),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Loss and training loop
# ─────────────────────────────────────────────────────────────────────────────

def _compute_data_loss_exp(model: InstrumentProfileExp, b: dict,
                           return_breakdown: bool = False):
    """
    Data-fit loss for experimental model (vel passed to all networks).

    Args:
        return_breakdown: if True, returns (total_loss, {name: scalar}) instead
                          of just total_loss. Used for verbose logging.
    """
    named = {}

    if "B_mf" in b:
        named["B"] = nn.functional.mse_loss(
            model.forward_B(b["B_mf"], b["B_vf"]).squeeze(-1), b["B_y"])

    if "dur_mf" in b:
        pred  = model.forward_dur(b["dur_mf"], b["dur_vf"]).squeeze(-1)
        dur_w = torch.exp(b["dur_y"]*0.1); dur_w /= dur_w.mean()
        named["dur"] = (dur_w*(pred - b["dur_y"])**2).mean()

    if "wf_mf" in b:
        named["wf"] = nn.functional.mse_loss(
            model.forward_wf(b["wf_mf"], b["wf_vf"]).squeeze(-1), b["wf_y"])

    if "tk1_mf" in b:
        pred = model.forward_tau1_k1(b["tk1_mf"], b["tk1_vf"]).squeeze(-1)
        named["tau1"] = 2.0 * nn.functional.mse_loss(pred, b["tk1_y"])

    if "tau_mf" in b:
        pred = model.forward_tau_ratio(b["tau_mf"], b["tau_kf"], b["tau_vf"]).squeeze(-1)
        named["tau_r"] = (b["tau_w"] * nn.functional.huber_loss(
            pred, b["tau_y"], delta=0.3, reduction="none")).mean()

    if "a0_mf" in b:
        named["A0"] = nn.functional.mse_loss(
            model.forward_A0(b["a0_mf"], b["a0_kf"], b["a0_vf"]).squeeze(-1),
            b["a0_y"])

    if "df_mf" in b:
        named["df"] = nn.functional.mse_loss(
            model.forward_df(b["df_mf"], b["df_kf"], b["df_vf"]).squeeze(-1),
            b["df_y"])

    if "eq_mf" in b:
        named["eq"] = 0.1 * nn.functional.mse_loss(
            model.forward_eq(b["eq_mf"], b["eq_ff"], b["eq_vf"]).squeeze(-1),
            b["eq_y"])

    if "noise_mf" in b:
        named["noise"] = nn.functional.mse_loss(
            model.forward_noise(b["noise_mf"], b["noise_vf"]), b["noise_y"])

    if "biexp_mf" in b:
        named["tau2"] = nn.functional.mse_loss(
            model.forward_biexp(b["biexp_mf"], b["biexp_kf"], b["biexp_vf"]),
            b["biexp_y"])

    total = sum(named.values()) / len(named) if named else torch.tensor(0.0)
    return (total, named) if return_breakdown else total


def _expand_head(head: nn.Sequential) -> nn.Sequential:
    """
    Net2net identity expansion: insert Linear(h,h)_identity + SiLU before the
    output layer.  Preserves the function at initialisation; subsequent gradient
    steps can then specialise the new layer.

    Works on any Sequential produced by mlp() with ≥ 1 hidden layer.
    """
    mods = list(head.children())
    # Find index of the last Linear (= output layer)
    last_idx = max(i for i, m in enumerate(mods) if isinstance(m, nn.Linear))
    h = mods[last_idx].in_features

    identity = nn.Linear(h, h)
    with torch.no_grad():
        nn.init.eye_(identity.weight)
        # small noise to break symmetry between units
        identity.weight.add_(torch.randn_like(identity.weight) * 0.01)
        nn.init.zeros_(identity.bias)

    new_mods = mods[:last_idx] + [identity, nn.SiLU(), mods[last_idx]]
    return nn.Sequential(*new_mods)


def _expand_all_heads(model: "InstrumentProfileEncExpWithB") -> int:
    """
    Expand every head in the model by one layer (identity init).
    Returns number of heads expanded.
    """
    head_names = [
        "B_head", "dur_head", "tau1_k1_head", "wf_head",
        "noise_head", "phi_head", "tau_ratio_head", "A0_head",
        "df_head", "eq_head", "biexp_head",
    ]
    count = 0
    for name in head_names:
        head = getattr(model, name, None)
        if head is not None:
            setattr(model, name, _expand_head(head))
            count += 1
    return count


def _run_training_exp(
    model:             InstrumentProfileExp,
    ds:                dict,
    val_ds:            dict  = None,
    epochs:            int   = 10000,
    lr:                float = 3e-3,
    eval_every:        int   = 50,
    verbose:           bool  = True,
    plateau_patience:  int   = 10,   # eval intervals without improvement → expand
    plateau_min_delta: float = 0.002,
    max_expansions:    int   = 2,
    all_params:        dict  = None,  # full params dict, needed by icr_evaluator
    icr_evaluator             = None,  # ICRBatchEvaluator or None
    icr_patience:      int   = 15,    # evals without ICR-MRSTFT improvement → stop
) -> list:
    opt   = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs, eta_min=lr*0.01)
    b     = ds["batches"]
    losses = []

    best_val   = float("inf")
    best_state = copy.deepcopy(model.state_dict())

    # ICR-MRSTFT early stopping (active when icr_evaluator is provided)
    best_icr_mrstft  = float("inf")
    best_icr_state   = copy.deepcopy(model.state_dict())
    _icr_no_improve  = 0

    # Plateau / progressive depth tracking (used only without icr_evaluator)
    _plateau_count  = 0
    _expansions     = 0

    # Running averages of smooth penalties for verbose logging
    _sm_midi_avg = 0.0
    _sm_vel_avg  = 0.0
    _sm_count    = 0

    for epoch in range(1, epochs+1):
        model.train()
        opt.zero_grad()

        loss = _compute_data_loss_exp(model, b)

        # Smoothness penalty over MIDI grid (every 5 epochs)
        # MIDI smoothness averaged over all 8 velocity layers; vel smoothness at MIDI=60
        if epoch % 5 == 0:
            midi_grid = torch.arange(21, 108, dtype=torch.float32)
            mf_grid   = torch.stack([midi_feat(float(m)) for m in midi_grid])
            kf_ref    = k_feat(1).unsqueeze(0).expand(len(midi_grid), -1)

            # MIDI smoothness averaged over all 8 velocity layers
            _sm = []
            for _v in range(8):
                vf = vel_feat(_v).unsqueeze(0).expand(len(midi_grid), -1)
                tau_g = model.forward_tau1_k1(mf_grid, vf).squeeze(-1)
                a0_g  = model.forward_A0(mf_grid, kf_ref, vf).squeeze(-1)
                n_g   = model.forward_noise(mf_grid, vf)
                _sm.append((tau_g[1:]-tau_g[:-1]).pow(2).mean()
                           + (a0_g[1:]-a0_g[:-1]).pow(2).mean()
                           + (n_g[1:]-n_g[:-1]).pow(2).mean())
            smooth_midi = torch.stack(_sm).mean()

            # Velocity smoothness at MIDI=60 (light — allow vel variation)
            vel_grid = torch.arange(8, dtype=torch.float32)
            mf60     = midi_feat(60.0).unsqueeze(0).expand(len(vel_grid), -1)
            vf_grid  = torch.stack([vel_feat(int(v)) for v in vel_grid])
            dur_v    = model.forward_dur(mf60, vf_grid).squeeze(-1)
            smooth_vel = (dur_v[1:]-dur_v[:-1]).pow(2).mean()

            loss = loss + 0.3*smooth_midi + 0.3*smooth_vel

            _sm_midi_avg += float(smooth_midi.detach())
            _sm_vel_avg  += float(smooth_vel.detach())
            _sm_count    += 1

        loss.backward(); opt.step(); sched.step()
        loss_val = float(loss.detach())
        losses.append(loss_val)

        # Epoch-1 sanity check + periodic heartbeat
        if epoch == 1:
            print(f"  epoch    1/{epochs}  loss={loss_val:.4f}  [training started]", flush=True)
        elif verbose and epoch % 10 == 0 and epoch % eval_every != 0:
            import math as _math
            print(f"  epoch {epoch:4d}/{epochs}  loss={loss_val:.4f}"
                  + ("  [NaN!]" if _math.isnan(loss_val) else ""), flush=True)

        # Validation + verbose breakdown
        if val_ds and (epoch % eval_every == 0 or epoch == epochs):
            model.eval()
            with torch.no_grad():
                val_loss, val_bd = _compute_data_loss_exp(
                    model, val_ds["batches"], return_breakdown=True)
                val_loss = val_loss.item()
            if val_loss < best_val - plateau_min_delta:
                best_val        = val_loss
                best_state      = copy.deepcopy(model.state_dict())
                improved        = " ✓"
                _plateau_count  = 0
            else:
                improved       = ""
                _plateau_count += 1

            # ── ICR-MRSTFT eval (icr-eval mode) ──────────────────────────────
            if icr_evaluator is not None and all_params is not None:
                model.eval()
                with torch.no_grad():
                    icr_score = icr_evaluator.eval(model, all_params)
                model.train()
                if icr_score < best_icr_mrstft - 1e-4:
                    best_icr_mrstft = icr_score
                    best_icr_state  = copy.deepcopy(model.state_dict())
                    _icr_no_improve = 0
                else:
                    _icr_no_improve += 1
                if _icr_no_improve >= icr_patience:
                    if verbose:
                        print(f"  Early stop: ICR-MRSTFT no improvement for "
                              f"{icr_patience} evals (best={best_icr_mrstft:.4f})")
                    break

            # ── Plateau → expand heads ───────────────────────────────────────
            if (_plateau_count >= plateau_patience
                    and _expansions < max_expansions
                    and epoch < epochs - eval_every):
                n = _expand_all_heads(model)
                _expansions    += 1
                _plateau_count  = 0
                _icr_no_improve = 0   # give expanded model a fresh patience budget
                new_lr          = lr * (0.5 ** _expansions)
                opt   = optim.Adam(model.parameters(), lr=new_lr, weight_decay=1e-4)
                remaining = epochs - epoch
                sched = optim.lr_scheduler.CosineAnnealingLR(
                    opt, T_max=remaining, eta_min=new_lr*0.01)
                # Refresh best_state with newly expanded model
                best_state = copy.deepcopy(model.state_dict())
                if verbose:
                    print(f"  *** PLATEAU — expanded {n} heads "
                          f"(expansion {_expansions}/{max_expansions})  "
                          f"new_lr={new_lr:.2e}  remaining={remaining} epochs ***")

            if verbose:
                # Per-network val losses
                bd_str = "  ".join(
                    f"{k}={float(v):.4f}" for k, v in val_bd.items())
                # Smooth penalty averages since last report
                if _sm_count > 0:
                    sm_str = (f"  sm_midi={_sm_midi_avg/_sm_count:.4f}"
                              f"  sm_vel={_sm_vel_avg/_sm_count:.4f}")
                    _sm_midi_avg = _sm_vel_avg = 0.0; _sm_count = 0
                else:
                    sm_str = ""
                print(f"  epoch {epoch:4d}/{epochs}  "
                      f"train={loss_val:.4f}  val={val_loss:.4f}"
                      f"  lr={sched.get_last_lr()[0]:.2e}{improved}", flush=True)
                print(f"    val breakdown:  {bd_str}{sm_str}", flush=True)
        elif verbose and epoch % eval_every == 0:
            _, train_bd = _compute_data_loss_exp(model, b, return_breakdown=True)
            bd_str = "  ".join(
                f"{k}={float(v):.4f}" for k, v in train_bd.items())
            if _sm_count > 0:
                sm_str = (f"  sm_midi={_sm_midi_avg/_sm_count:.4f}"
                          f"  sm_vel={_sm_vel_avg/_sm_count:.4f}")
                _sm_midi_avg = _sm_vel_avg = 0.0; _sm_count = 0
            else:
                sm_str = ""
            print(f"  epoch {epoch:4d}/{epochs}  loss={loss_val:.6f}  "
                  f"lr={sched.get_last_lr()[0]:.2e}", flush=True)
            print(f"    breakdown:  {bd_str}{sm_str}", flush=True)

    if icr_evaluator is not None:
        model.load_state_dict(best_icr_state)
        print(f"  Restored best ICR-MRSTFT checkpoint ({best_icr_mrstft:.4f})")
    elif val_ds:
        model.load_state_dict(best_state)
        print(f"  Restored best checkpoint (val={best_val:.4f})")

    return losses


# ─────────────────────────────────────────────────────────────────────────────
# Profile generation (inference — vel now passed to all networks)
# ─────────────────────────────────────────────────────────────────────────────

def generate_profile_exp(
    model:        InstrumentProfileExp,
    ds:           dict,
    midi_from:    int  = 21,
    midi_to:      int  = 108,
    sr:           int  = 44_100,
    orig_samples: dict = None,
) -> dict:
    """
    Evaluate InstrumentProfileExp at all (midi, vel) positions.

    Dispatcher: if the model carries a ``_b_fitter`` attribute (set by
    ProfileTrainerEncExp.train), delegates to generate_profile_exp_no_b so
    that B is sourced from the spline rather than the NN.  This keeps all
    callers (SoundbankExporter, pipelines) working without changes.

    Like generate_profile but B, dur, wf, tau_ratio, df, eq are computed
    per-velocity (inner loop over vel moved to enclose all network calls).
    """
    b_fitter = getattr(model, "_b_fitter", None)
    if b_fitter is not None:
        return generate_profile_exp_no_b(
            model, ds, b_fitter,
            midi_from=midi_from, midi_to=midi_to,
            sr=sr, orig_samples=orig_samples,
        )

    model.eval()
    samples_out = {}
    eq_freqs    = ds.get("eq_freqs")

    with torch.no_grad():
        for midi in range(midi_from, midi_to+1):
            mf = midi_feat(midi)
            f0 = midi_to_hz(midi)
            n_partials = max(1, int((sr/2)/f0))

            for vel in range(8):
                vf  = vel_feat(vel)
                key = f"m{midi:03d}_vel{vel}"

                B   = float(np.clip(float(torch.exp(model.forward_B(mf, vf)).item()),   1e-8, None))
                dur = float(np.clip(float(torch.exp(model.forward_dur(mf, vf)).item()), 0.3,  None))
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
                for k in range(1, n_partials+1):
                    kf  = k_feat(k)
                    f_k = k*f0*math.sqrt(1.0 + B*k**2)
                    if f_k >= sr/2: break

                    tau1_k1 = float(torch.exp(model.forward_tau1_k1(mf, vf)).item())
                    if k == 1:
                        tau1 = tau1_k1
                    else:
                        log_ratio  = float(model.forward_tau_ratio(mf, kf, vf).item())
                        log_k_bias = -0.3*math.log(k)
                        log_ratio  = max(log_k_bias-2.0, min(0.0, log_ratio))
                        tau1 = tau1_k1*math.exp(log_ratio)
                    tau1 = max(tau1, 0.005)

                    biexp    = model.forward_biexp(mf, kf, vf).squeeze(0)
                    a1_val   = float(np.clip(float(torch.sigmoid(biexp[0]).item()), 0.05, 0.99))
                    tau2_val = tau1*max(float(torch.exp(biexp[1]).item()), 3.0)
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
# ProfileTrainerExp
# ─────────────────────────────────────────────────────────────────────────────

class ProfileTrainerExp(ProfileTrainer):
    """
    Train InstrumentProfileExp — velocity added to all sub-networks.

    API is identical to ProfileTrainer:
        model = ProfileTrainerExp().train(params, epochs=3000)
        model = ProfileTrainerExp().load("profile-exp.pt")
    """

    def train(
        self,
        params:   dict,
        epochs:   int   = 3000,
        hidden:   int   = 64,
        lr:       float = 0.003,
        val_frac: float = 0.15,
        verbose:  bool  = True,
    ) -> InstrumentProfileExp:
        samples  = params["samples"]
        measured = {k: v for k, v in samples.items() if not v.get("_interpolated")}

        train_s, val_s = _split_val_midis(measured, val_frac)
        val_midis = sorted({s["midi"] for s in val_s.values()})
        print(f"ProfileTrainerExp: {len(measured)} measured samples  "
              f"→  train={len(train_s)}  val={len(val_s)} "
              f"(MIDI {val_midis[0]}–{val_midis[-1]}, every ~{len(measured)//max(len(val_s),1)}th)")
        print("  Mode: experimental — velocity input on all 10 sub-networks")

        print("Building datasets …")
        train_ds = build_dataset_exp(train_s)
        val_ds   = build_dataset_exp(val_s)
        model    = InstrumentProfileExp(hidden=hidden)

        n_p = sum(p.numel() for p in model.parameters())
        print(f"Model parameters: {n_p:,}")
        print(f"Training {epochs} epochs …")

        _run_training_exp(model, train_ds, val_ds=val_ds,
                          epochs=epochs, lr=lr, verbose=verbose)
        model.eval()
        return model

    def load(self, path: str) -> InstrumentProfileExp:
        ckpt   = torch.load(path, map_location="cpu", weights_only=False)
        hidden = ckpt.get("hidden", 64)
        model  = InstrumentProfileExp(hidden=hidden)

        missing, unexpected = model.load_state_dict(ckpt["state_dict"], strict=False)
        if missing:
            print(f"  [load] new params (fresh init): {missing}")
        if unexpected:
            print(f"  [load] unexpected keys (ignored): {unexpected}")

        model.eval()
        return model

    def predict_all(
        self,
        model:     InstrumentProfileExp,
        midi_from: int = 21,
        midi_to:   int = 108,
        sr:        int = 44_100,
    ) -> dict:
        ds      = {"batches": {}, "eq_freqs": None}
        samples = generate_profile_exp(
            model, ds,
            midi_from=midi_from, midi_to=midi_to,
            sr=sr, orig_samples=None,
        )
        return {"samples": samples, "n_samples": len(samples)}


# ─────────────────────────────────────────────────────────────────────────────
# InstrumentProfileEncExpWithB — shared per-axis encoders
# ─────────────────────────────────────────────────────────────────────────────

class InstrumentProfileEncExpWithB(InstrumentProfileExp):
    """
    Like InstrumentProfileExp (all nets take velocity) but with shared encoders
    per axis: midi_enc, vel_enc, k_enc, freq_enc.

    Architecture:
        shared  midi_enc:  MLP(MIDI_DIM → ENC_MIDI)   — shared by all 11 heads
        shared  vel_enc:   MLP(VEL_DIM  → ENC_VEL)    — shared by all heads except B_head
        shared  k_enc:     MLP(K_DIM    → ENC_K)       — shared by k-dependent heads
        shared  freq_enc:  MLP(FREQ_DIM → ENC_FREQ)    — shared by eq_head

        heads: 2-layer MLP(enc_concat → hidden → output)

    Forward method signatures are identical to InstrumentProfileExp →
    build_dataset_exp / _compute_data_loss_exp / _run_training_exp /
    generate_profile_exp all work without modification.

    Gradient from every sub-network flows back through the shared encoders,
    forcing a jointly-useful representation per axis.
    """

    # Encoder output dimensions
    ENC_MIDI = 16
    ENC_VEL  = 8
    ENC_K    = 8
    ENC_FREQ = 8

    def __init__(self, hidden: int = 64, head_hidden: int = 32):
        nn.Module.__init__(self)       # skip InstrumentProfileExp.__init__

        dm = self.ENC_MIDI
        dv = self.ENC_VEL
        dk = self.ENC_K
        df = self.ENC_FREQ
        hh = head_hidden

        # ── Shared per-axis encoders (expressive — trained by all heads) ──────
        self.midi_enc = mlp(MIDI_DIM, hidden,      dm, layers=3)
        self.vel_enc  = mlp(VEL_DIM,  hidden // 2, dv, layers=2)
        self.k_enc    = mlp(K_DIM,    hidden // 2, dk, layers=2)
        self.freq_enc = mlp(FREQ_DIM, hidden // 2, df, layers=2)

        # ── Heads: shallow start (layers=1 simple, layers=2 k-dependent) ──────
        # Simple heads: ~320 training points → lean 1-layer readout
        # B: string-stiffness constant — physically independent of velocity
        self.B_head         = mlp(dm,           hh, 1, layers=1)
        self.dur_head       = mlp(dm + dv,      hh, 1, layers=1)
        self.tau1_k1_head   = mlp(dm + dv,      hh, 1, layers=1)
        self.wf_head        = mlp(dm + dv,      hh, 1, layers=1)
        self.noise_head     = mlp(dm + dv,      hh, 3, layers=1)
        self.phi_head       = mlp(dm + dv,      hh, 1, layers=1)
        # k-dependent heads: ~2800-5100 training points → 2-layer
        self.tau_ratio_head = mlp(dm + dk + dv, hh, 1, layers=2)
        self.A0_head        = mlp(dm + dk + dv, hh, 1, layers=2)
        self.df_head        = mlp(dm + dk + dv, hh, 1, layers=2)
        self.eq_head        = mlp(dm + df + dv, hh, 1, layers=2)
        self.biexp_head     = mlp(dm + dk + dv, hh, 2, layers=2)

        # Physically motivated initial biases
        nn.init.constant_(self.B_head[-1].bias,         -9.2)
        nn.init.constant_(self.noise_head[-1].bias[0],  -3.0)
        nn.init.constant_(self.noise_head[-1].bias[1],   8.0)
        nn.init.constant_(self.noise_head[-1].bias[2],  -2.8)
        nn.init.constant_(self.biexp_head[-1].bias[0],   1.73)
        nn.init.constant_(self.biexp_head[-1].bias[1],   1.10)
        nn.init.constant_(self.phi_head[-1].bias,        0.0)

    # ── Forward methods: encode each axis, then run head ─────────────────────

    def forward_B(self, mf, vf=None):
        return self.B_head(self.midi_enc(mf))   # vel ignored — B is velocity-independent

    def forward_dur(self, mf, vf):
        return self.dur_head(torch.cat([self.midi_enc(mf), self.vel_enc(vf)], -1))

    def forward_wf(self, mf, vf):
        return self.wf_head(torch.cat([self.midi_enc(mf), self.vel_enc(vf)], -1))

    def forward_tau1_k1(self, mf, vf):
        return self.tau1_k1_head(torch.cat([self.midi_enc(mf), self.vel_enc(vf)], -1))

    def forward_tau_ratio(self, mf, kf, vf):
        return self.tau_ratio_head(torch.cat(
            [self.midi_enc(mf), self.k_enc(kf), self.vel_enc(vf)], -1))

    def forward_A0(self, mf, kf, vf):
        return self.A0_head(torch.cat(
            [self.midi_enc(mf), self.k_enc(kf), self.vel_enc(vf)], -1))

    def forward_df(self, mf, kf, vf):
        return self.df_head(torch.cat(
            [self.midi_enc(mf), self.k_enc(kf), self.vel_enc(vf)], -1))

    def forward_eq(self, mf, ff, vf):
        return self.eq_head(torch.cat(
            [self.midi_enc(mf), self.freq_enc(ff), self.vel_enc(vf)], -1))

    def forward_noise(self, mf, vf):
        return self.noise_head(torch.cat([self.midi_enc(mf), self.vel_enc(vf)], -1))

    def forward_biexp(self, mf, kf, vf):
        return self.biexp_head(torch.cat(
            [self.midi_enc(mf), self.k_enc(kf), self.vel_enc(vf)], -1))

    def forward_phi(self, mf, vf):
        return self.phi_head(torch.cat([self.midi_enc(mf), self.vel_enc(vf)], -1))


# ─────────────────────────────────────────────────────────────────────────────
# ProfileTrainerEncExpWithB
# ─────────────────────────────────────────────────────────────────────────────

class ProfileTrainerEncExpWithB(ProfileTrainerExp):
    """
    Train InstrumentProfileEncExpWithB — shared per-axis encoders + velocity on all nets.

    Reuses build_dataset_exp / _compute_data_loss_exp / _run_training_exp /
    generate_profile_exp from the experimental module unchanged.

    API identical to ProfileTrainerExp:
        model = ProfileTrainerEncExpWithB().train(params, epochs=3000)
        model = ProfileTrainerEncExpWithB().load("profile-enc-exp.pt")
    """

    def train(
        self,
        params:        dict,
        epochs:        int   = 10000,
        hidden:        int   = 64,
        lr:            float = 0.003,
        val_frac:      float = 0.15,
        verbose:       bool  = True,
        icr_evaluator         = None,
        icr_patience:  int   = 15,
    ) -> InstrumentProfileEncExpWithB:
        samples  = params["samples"]
        measured = {k: v for k, v in samples.items() if not v.get("_interpolated")}

        train_s, val_s = _split_val_midis(measured, val_frac)
        val_midis = sorted({s["midi"] for s in val_s.values()})
        print(f"ProfileTrainerEncExpWithB: {len(measured)} measured samples  "
              f"→  train={len(train_s)}  val={len(val_s)} "
              f"(MIDI {val_midis[0]}–{val_midis[-1]}, every ~{len(measured)//max(len(val_s),1)}th)")
        print(f"  Mode: experimental-enc — shared encoders "
              f"(midi→{InstrumentProfileEncExpWithB.ENC_MIDI}  "
              f"vel→{InstrumentProfileEncExpWithB.ENC_VEL}  "
              f"k→{InstrumentProfileEncExpWithB.ENC_K}  "
              f"freq→{InstrumentProfileEncExpWithB.ENC_FREQ})")
        if icr_evaluator is not None:
            print(f"  Eval: ICR-MRSTFT (early stop patience={icr_patience} evals, "
                  f"no MRSTFTFinetuner)")

        print("Building datasets ...", flush=True)
        train_ds = build_dataset_exp(train_s)
        val_ds   = build_dataset_exp(val_s)
        _ds_sizes = {k: v.shape[0] for k, v in train_ds["batches"].items() if hasattr(v, "shape")}
        print(f"  train batches: { {k: v for k, v in _ds_sizes.items()} }", flush=True)
        model    = InstrumentProfileEncExpWithB(hidden=hidden, head_hidden=32)

        n_p = sum(p.numel() for p in model.parameters())
        print(f"Model parameters: {n_p:,}  (encoders hidden={hidden}, heads hidden=32)", flush=True)
        if icr_evaluator is not None:
            print(f"Training {epochs} epochs  (ICR-MRSTFT early stop) ...", flush=True)
        else:
            print(f"Training {epochs} epochs  (progressive depth: expand heads at plateau) ...", flush=True)

        _run_training_exp(model, train_ds, val_ds=val_ds,
                          epochs=epochs, lr=lr, verbose=verbose,
                          all_params=params,
                          icr_evaluator=icr_evaluator,
                          icr_patience=icr_patience)
        model.eval()
        return model

    def load(self, path: str) -> InstrumentProfileEncExpWithB:
        ckpt   = torch.load(path, map_location="cpu", weights_only=False)
        hidden = ckpt.get("hidden", 64)
        model  = InstrumentProfileEncExpWithB(hidden=hidden)

        missing, unexpected = model.load_state_dict(ckpt["state_dict"], strict=False)
        if missing:
            print(f"  [load] new params (fresh init): {missing}")
        if unexpected:
            print(f"  [load] unexpected keys (ignored): {unexpected}")

        model.eval()
        return model

    def predict_all(
        self,
        model:     InstrumentProfileEncExpWithB,
        midi_from: int = 21,
        midi_to:   int = 108,
        sr:        int = 44_100,
    ) -> dict:
        ds      = {"batches": {}, "eq_freqs": None}
        samples = generate_profile_exp(
            model, ds,
            midi_from=midi_from, midi_to=midi_to,
            sr=sr, orig_samples=None,
        )
        return {"samples": samples, "n_samples": len(samples)}


# ─────────────────────────────────────────────────────────────────────────────
# B-spline helpers (imported here so callers only need profile_trainer_exp)
# ─────────────────────────────────────────────────────────────────────────────

from training.modules.b_spline_fitter import BSplneFitter   # noqa: E402


def build_dataset_exp_no_b(samples: dict) -> dict:
    """
    Like build_dataset_exp but removes B_mf / B_vf / B_y from the batch dict.

    Since _compute_data_loss_exp checks ``if "B_mf" in b:`` before computing
    the B loss term, removing these keys eliminates B from the gradient.
    """
    ds = build_dataset_exp(samples)
    for key in ("B_mf", "B_vf", "B_y"):
        ds["batches"].pop(key, None)
    ds["n_B"] = 0
    return ds


def generate_profile_exp_no_b(
    model:        "InstrumentProfileEncExp",
    ds:           dict,
    b_fitter:     BSplneFitter,
    midi_from:    int  = 21,
    midi_to:      int  = 108,
    sr:           int  = 44_100,
    orig_samples: dict = None,
) -> dict:
    """
    Like generate_profile_exp_with_b but B is sourced from b_fitter (1-D spline
    over MIDI) instead of the NN.

    B is evaluated once per MIDI and shared across all 8 velocity layers —
    consistent with its physical interpretation as velocity-independent.
    """
    model.eval()
    samples_out = {}
    eq_freqs    = ds.get("eq_freqs")

    with torch.no_grad():
        for midi in range(midi_from, midi_to + 1):
            mf = midi_feat(midi)
            f0 = midi_to_hz(midi)
            n_partials = max(1, int((sr / 2) / f0))

            B = b_fitter.predict(midi)   # one value per MIDI, all velocities

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
# InstrumentProfileEncExp — default model (B from spline, not from NN)
# ─────────────────────────────────────────────────────────────────────────────

class InstrumentProfileEncExp(InstrumentProfileEncExpWithB):
    """
    Default instrument profile model — identical to InstrumentProfileEncExpWithB
    except that B_head is removed.

    B (inharmonicity) is physically velocity-independent (string stiffness) and
    its extracted values have high per-note variance, causing B loss to dominate
    the multi-task gradient (~5–8× other terms).  Removing B_head:

        - Frees ~1 K params + optimizer state.
        - Redirects shared midi_enc gradient capacity to the 10 remaining heads.
        - Eliminates the dominant noise source from training targets.

    B is supplied at inference time by BSplneFitter (a smooth 1-D spline over
    MIDI fitted from measured notes) stored on the model as ``_b_fitter``.
    generate_profile_exp detects this attribute and dispatches automatically to
    generate_profile_exp_no_b.

    To retain the old B-predicting behaviour use InstrumentProfileEncExpWithB /
    ProfileTrainerEncExpWithB explicitly.
    """

    def __init__(self, hidden: int = 64, head_hidden: int = 32):
        super().__init__(hidden=hidden, head_hidden=head_hidden)
        del self.B_head

    def forward_B(self, mf, vf=None):
        """Return zeros — B smooth-penalty contribution is zero (B is smooth by spline)."""
        n = mf.shape[0] if mf.dim() > 1 else 1
        return torch.zeros(n, 1, dtype=mf.dtype, device=mf.device)


# ─────────────────────────────────────────────────────────────────────────────
# ProfileTrainerEncExp — default trainer (auto-fits B spline)
# ─────────────────────────────────────────────────────────────────────────────

class ProfileTrainerEncExp(ProfileTrainerEncExpWithB):
    """
    Default trainer — uses InstrumentProfileEncExp (no B_head) and auto-fits a
    BSplneFitter from measured notes before training.

    B spline is attached to the returned model as ``model._b_fitter`` so that
    generate_profile_exp and SoundbankExporter.hybrid() dispatch correctly
    without any changes to the callers.

    API is identical to ProfileTrainerEncExpWithB:
        model = ProfileTrainerEncExp().train(params, epochs=5000)

    Pass an explicit ``b_fitter`` to reuse a spline fitted elsewhere:
        b_fitter = BSplneFitter().fit(params["samples"])
        model    = ProfileTrainerEncExp().train(params, b_fitter=b_fitter)
    """

    def train(
        self,
        params:        dict,
        b_fitter:      BSplneFitter = None,
        epochs:        int   = 10000,
        hidden:        int   = 64,
        lr:            float = 0.003,
        val_frac:      float = 0.15,
        verbose:       bool  = True,
        icr_evaluator         = None,
        icr_patience:  int   = 15,
    ) -> InstrumentProfileEncExp:
        from training.modules.profile_trainer import _split_val_midis

        samples  = params["samples"]
        measured = {k: v for k, v in samples.items() if not v.get("_interpolated")}

        if b_fitter is None:
            print("  Fitting B spline from measured notes (auto) ...")
            b_fitter = BSplneFitter().fit(measured)

        train_s, val_s = _split_val_midis(measured, val_frac)
        val_midis = sorted({s["midi"] for s in val_s.values()})
        enc = InstrumentProfileEncExpWithB
        print(
            f"ProfileTrainerEncExp: {len(measured)} measured samples  "
            f"→  train={len(train_s)}  val={len(val_s)} "
            f"(MIDI {val_midis[0]}–{val_midis[-1]}, every ~{len(measured)//max(len(val_s),1)}th)"
        )
        print(
            f"  Mode: enc (B=spline) — shared encoders "
            f"(midi→{enc.ENC_MIDI}  vel→{enc.ENC_VEL}  k→{enc.ENC_K}  freq→{enc.ENC_FREQ})"
        )
        if icr_evaluator is not None:
            print(f"  Eval: ICR-MRSTFT (early stop patience={icr_patience} evals, no MRSTFTFinetuner)")

        print("Building datasets (B excluded from targets) ...", flush=True)
        train_ds = build_dataset_exp_no_b(train_s)
        val_ds   = build_dataset_exp_no_b(val_s)
        _ds_sizes = {k: v.shape[0] for k, v in train_ds["batches"].items() if hasattr(v, "shape")}
        print(f"  train batches: { {k: v for k, v in _ds_sizes.items()} }", flush=True)

        model = InstrumentProfileEncExp(hidden=hidden, head_hidden=32)
        n_p   = sum(p.numel() for p in model.parameters())
        print(f"Model parameters: {n_p:,}  (no B_head; B from spline)", flush=True)

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
        model._b_fitter = b_fitter   # attached for generate_profile_exp / SoundbankExporter
        model.eval()
        return model

    def load(self, path: str) -> InstrumentProfileEncExp:
        ckpt   = torch.load(path, map_location="cpu", weights_only=False)
        hidden = ckpt.get("hidden", 64)
        model  = InstrumentProfileEncExp(hidden=hidden)

        missing, unexpected = model.load_state_dict(ckpt["state_dict"], strict=False)
        if missing:
            print(f"  [load] new params (fresh init): {missing}")
        if unexpected:
            print(f"  [load] unexpected keys (ignored): {unexpected}")

        model.eval()
        return model
