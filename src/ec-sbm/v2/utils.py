import logging
from pathlib import Path


def normalize_edge(u, v):
    """Return a canonical (min, max) edge tuple."""
    return (min(u, v), max(u, v))


def run_rewire_attempts(invalid_edges, process_one_edge, max_retries=10):
    """
    Outer retry loop for 2-opt edge rewiring.

    Repeatedly passes invalid edges to `process_one_edge` until all edges are
    resolved or `max_retries` attempts have been exhausted.  Within each
    attempt, a recycle counter tracks whether the pass through the deque is
    making progress: if `len(invalid_edges)` has not decreased after cycling
    through all remaining edges, the inner pass ends early (stagnation).

    Args:
        invalid_edges (deque): Edges that could not be placed without creating
            self-loops or duplicates.
        process_one_edge(e, invalid_edges) -> bool: Callback invoked with the
            popped edge and the live deque.  Must re-append `e` if unresolved.
            Return True to break the current inner pass, False to continue.
        max_retries (int): Maximum number of outer passes before giving up.
    """
    for attempt in range(max_retries):
        if not invalid_edges:
            logging.info("All bad edges resolved! Exiting rewiring loop early.")
            break

        last_recycle = len(invalid_edges)
        recycle_counter = last_recycle

        while invalid_edges:
            recycle_counter -= 1
            if recycle_counter < 0:
                if len(invalid_edges) < last_recycle:
                    last_recycle = len(invalid_edges)
                    recycle_counter = last_recycle
                else:
                    break

            e = invalid_edges.popleft()
            if process_one_edge(e, invalid_edges):
                break

        logging.info(
            f"After attempt {attempt + 1}: {len(invalid_edges)} bad edges remain."
        )


def setup_logging(log_filepath: Path):
    """
    Universal logging function.
    Forces output exclusively to the provided log_filepath with timestamps.
    Prevents any standard error/console leakage.
    """
    log_filepath.parent.mkdir(parents=True, exist_ok=True)

    # 1. Get the root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # 2. Strip out ALL existing handlers (this prevents the stderr leakage)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # 3. Create a dedicated FileHandler
    file_handler = logging.FileHandler(log_filepath, mode="w")
    file_handler.setLevel(logging.INFO)

    # 4. Enforce the timestamp format
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)

    # 5. Attach our strict file handler to the root logger
    logger.addHandler(file_handler)
