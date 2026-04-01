"""
training/pipeline_full.py
───────────────────────────
Extract → filter → EQ → train NN → finetune → export hybrid soundbank.

Call via train_pipeline.py or import directly:
    from training.pipeline_full import run
    model, out_path = run(bank_dir, out_path, epochs=1800, ft_epochs=200)
"""

from training.modules.extractor       import ParamExtractor
from training.modules.outlier_filter  import OutlierFilter
from training.modules.eq_fitter       import EQFitter
from training.modules.profile_trainer import ProfileTrainer
from training.modules.mrstft_finetune import MRSTFTFinetuner
from training.modules.exporter        import SoundbankExporter


def run(bank_dir: str, out_path: str,
        epochs: int = 1800, ft_epochs: int = 200,
        workers: int = None) -> tuple:
    """
    Full pipeline: Extract → filter → EQ → train NN → finetune → export hybrid.

    Args:
        bank_dir:   Directory with WAV files.
        out_path:   Output JSON soundbank path.
        epochs:     NN training epochs.
        ft_epochs:  MRSTFT fine-tuning epochs.
        workers:    Parallel worker count (None = auto).

    Returns:
        (model, out_path) — trained InstrumentProfile and path to soundbank JSON.
    """
    params = ParamExtractor().extract_bank(bank_dir, workers)
    params = OutlierFilter().filter(params)
    params = EQFitter().fit_bank(params, bank_dir, workers)
    model  = ProfileTrainer().train(params, epochs=epochs)
    model  = MRSTFTFinetuner().finetune(model, bank_dir, epochs=ft_epochs)
    SoundbankExporter().hybrid(model, params, out_path)
    return model, out_path
