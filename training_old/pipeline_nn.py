"""
training/pipeline_nn.py
────────────────────────
Extract -> filter -> EQ -> train EncExp NN -> export hybrid.

Difference from pipeline_experimental:
    - No MRSTFTFinetuner step after NN training.
    - NN output is exported directly as hybrid soundbank.
    - Use this when you want pure parameter-space NN fitting with
      shared encoders, without the slow Python-proxy finetuner.
    - For ground-truth perceptual eval/early-stop use pipeline_icr_eval.

Call via run-training.py or import directly:
    from training.pipeline_nn import run
    model, out_path = run(bank_dir, out_path, epochs=10000)
"""

from training.modules.extractor                  import ParamExtractor
from training.modules.structural_outlier_filter  import StructuralOutlierFilter
from training.modules.eq_fitter                  import EQFitter
from training.modules.profile_trainer_exp        import ProfileTrainerEncExp
from training.modules.exporter                   import SoundbankExporter


def run(bank_dir: str, out_path: str,
        epochs: int = 10000,
        workers: int = None, skip_outliers: bool = False,
        sr_tag: str = "f48") -> tuple:
    """
    NN pipeline:
    Extract -> filter -> EQ -> train NN (shared encoders + vel on all nets)
    -> export hybrid.

    Args:
        bank_dir:       Directory with WAV files.
        out_path:       Output JSON soundbank path.
        epochs:         NN training epochs (default: 10000).
        workers:        Parallel worker count (None = auto).
        skip_outliers:  Skip structural outlier detection step.
        sr_tag:         Sample-rate tag suffix, e.g. "f44" or "f48".

    Returns:
        (model, out_path) -- trained InstrumentProfileEncExp and path to soundbank JSON.
    """
    params = ParamExtractor().extract_bank(bank_dir, workers, sr_tag=sr_tag)
    if not skip_outliers:
        params = StructuralOutlierFilter().filter(params)
    params = EQFitter().fit_bank(params, bank_dir, workers)
    model  = ProfileTrainerEncExp().train(params, epochs=epochs)
    SoundbankExporter().hybrid(model, params, out_path)
    return model, out_path
