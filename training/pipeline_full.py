"""
training/pipeline_full.py
───────────────────────────
Extract → filter → EQ → train NN → finetune → export hybrid soundbank.

Call via run-training.py or import directly:
    from training.pipeline_full import run
    model, out_path = run(bank_dir, out_path, epochs=3000, ft_epochs=200)
"""

from training.modules.extractor                  import ParamExtractor
from training.modules.structural_outlier_filter  import StructuralOutlierFilter
from training.modules.eq_fitter                  import EQFitter
from training.modules.profile_trainer            import ProfileTrainer
from training.modules.mrstft_finetune            import MRSTFTFinetuner
from training.modules.exporter                   import SoundbankExporter


def run(bank_dir: str, out_path: str,
        epochs: int = 3000, ft_epochs: int = 200,
        workers: int = None, skip_outliers: bool = False,
        sr_tag: str = "f48") -> tuple:
    """
    Full pipeline: Extract → filter → EQ → train NN → finetune → export hybrid.

    Args:
        bank_dir:       Directory with WAV files.
        out_path:       Output JSON soundbank path.
        epochs:         NN training epochs.
        ft_epochs:      MRSTFT fine-tuning epochs.
        workers:        Parallel worker count (None = auto).
        skip_outliers:  Skip structural outlier detection step.
        sr_tag:         Sample-rate tag suffix, e.g. "f44" or "f48".

    Returns:
        (model, out_path) — trained InstrumentProfile and path to soundbank JSON.
    """
    params = ParamExtractor().extract_bank(bank_dir, workers, sr_tag=sr_tag)
    if not skip_outliers:
        params = StructuralOutlierFilter().filter(params)
    params = EQFitter().fit_bank(params, bank_dir, workers)
    model  = ProfileTrainer().train(params, epochs=epochs)
    model  = MRSTFTFinetuner().finetune(model, bank_dir, epochs=ft_epochs)
    SoundbankExporter().hybrid(model, params, out_path)
    return model, out_path
