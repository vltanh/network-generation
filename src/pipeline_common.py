import time
import logging
from contextlib import contextmanager
from pathlib import Path

import pandas as pd


def setup_logging(log_filepath: Path):
    """
    Universal logging function.
    Forces output exclusively to the provided log_filepath with timestamps.
    Prevents any standard error/console leakage.
    """
    log_filepath.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    file_handler = logging.FileHandler(log_filepath, mode="w")
    file_handler.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)


def standard_setup(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(output_dir / "run.log")
    return output_dir


@contextmanager
def timed(label):
    start = time.perf_counter()
    yield
    logging.info(f"{label} elapsed: {time.perf_counter() - start:.4f} seconds")


def write_edge_tuples_csv(path, edges, node_iid2id=None):
    if node_iid2id is None:
        rows = [(int(s), int(t)) for s, t in edges]
    else:
        rows = [(node_iid2id[int(s)], node_iid2id[int(t)]) for s, t in edges]
    pd.DataFrame(rows, columns=["source", "target"]).to_csv(path, index=False)


def drop_singleton_clusters(com_df):
    counts = com_df["cluster_id"].value_counts()
    kept = counts[counts > 1].index
    n_dropped = len(counts) - len(kept)
    if n_dropped:
        logging.info(f"Dropping {n_dropped} singleton cluster(s) from com.csv")
    return com_df[com_df["cluster_id"].isin(kept)]
