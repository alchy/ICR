"""
training/pipeline_simple.py
─────────────────────────────
Extract → filter outliers → fit EQ → export soundbank.

Call via train_pipeline.py or import directly:
    from training.pipeline_simple import run
    out_path = run(bank_dir, out_path, workers=4)
"""

from training.modules.extractor                  import ParamExtractor
from training.modules.structural_outlier_filter  import StructuralOutlierFilter
from training.modules.eq_fitter                  import EQFitter
from training.modules.exporter                   import SoundbankExporter


def run(bank_dir: str, out_path: str,
        workers: int = None, skip_eq: bool = False,
        skip_outliers: bool = False,
        sr_tag: str = "f48") -> str:
    """
    Simple pipeline: Extract → filter outliers → fit EQ → export soundbank.

    Args:
        bank_dir:       Directory with WAV files.
        out_path:       Output JSON soundbank path.
        workers:        Parallel worker count (None = auto).
        skip_eq:        Skip spectral EQ step (faster, no body resonance).
        skip_outliers:  Skip structural outlier detection step.
        sr_tag:         Sample-rate tag suffix, e.g. "f44" or "f48".

    Returns:
        out_path (echoed for chaining).
    """
    params = ParamExtractor().extract_bank(bank_dir, workers, sr_tag=sr_tag)
    if not skip_outliers:
        params = StructuralOutlierFilter().filter(params)

    if not skip_eq:
        params = EQFitter().fit_bank(params, bank_dir, workers)

    SoundbankExporter().from_params(params, out_path)
    return out_path
