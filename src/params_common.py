"""Per-stage ``params.txt`` helpers.

Every pipeline stage records the output-affecting parameters it ran under
in ``<stage_dir>/params.txt``.  Format: one ``key=value`` per line, sorted
by key, bools rendered as lowercase ``true``/``false``.  Values are
stringified on write; the reader returns a plain ``dict[str, str]`` so
callers parse numeric/bool types themselves.

The file lives in the stage's ``mark_done`` **IN** list — changes to it
invalidate the stage's cache and cascade through downstream IN lists
(since downstream IN references the stage's OUT files, which change when
the stage re-runs).

Kept separate from ``profile_common`` because the helper is
pipeline-wide (every stage writes one), not profile-specific.
"""
from __future__ import annotations

from pathlib import Path


def _render(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def write_params(out_dir, **kwargs) -> None:
    """Write ``params.txt`` to ``out_dir``.

    Keys are sorted alphabetically; one ``key=value`` per line.  Bools
    render as lowercase ``true``/``false``; other types are stringified
    via ``str()``.  Raises ``ValueError`` if called with no kwargs — an
    empty ``params.txt`` means the caller forgot the stage's knobs.
    """
    if not kwargs:
        raise ValueError("write_params: at least one key=value required")
    out_path = Path(out_dir) / "params.txt"
    lines = [f"{k}={_render(kwargs[k])}" for k in sorted(kwargs)]
    out_path.write_text("\n".join(lines) + "\n")


def resolve_param(cli_value, file_params: dict[str, str] | None, key: str,
                  default=None, parser=None):
    """Resolve a stage knob under CLI-over-file-over-default precedence.

    Pipeline passes ``--params-file`` (file wins over default); standalone
    users pass individual flags (CLI wins over both). Pass ``None`` for
    ``cli_value`` when the user didn't supply the flag; ``file_params=None``
    when no params.txt was provided.

    ``parser`` is an optional str→T coercion applied to the file value (the
    file is always text); CLI values are returned as-is since argparse
    already typed them.
    """
    if cli_value is not None:
        return cli_value
    if file_params is not None and key in file_params:
        raw = file_params[key]
        return parser(raw) if parser else raw
    return default


def _parse_bool(text: str) -> bool:
    """Parse lowercase ``true``/``false`` (the format ``write_params`` emits)."""
    if text == "true":
        return True
    if text == "false":
        return False
    raise ValueError(f"expected 'true' or 'false', got {text!r}")


def read_params(path) -> dict[str, str]:
    """Read a ``params.txt`` produced by ``write_params``.

    Returns a ``dict[str, str]``; callers are responsible for parsing
    numeric/bool types.  Blank lines are ignored.  Raises ``ValueError``
    on malformed lines or duplicate keys.
    """
    text = Path(path).read_text()
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"{path}: expected key=value, got {raw!r}")
        key, value = line.split("=", 1)
        if key in out:
            raise ValueError(f"{path}: duplicate key {key!r}")
        out[key] = value
    return out
