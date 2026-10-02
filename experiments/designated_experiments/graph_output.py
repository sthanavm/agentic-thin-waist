"""Save every notebook Matplotlib figure into a run-local graphs directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


def install_graph_saver(
    pyplot: Any,
    result_dir: Path,
    graph_names: Iterable[str],
) -> Path:
    """Wrap ``plt.show`` so report plots are also saved with stable names."""
    graphs_dir = result_dir / "graphs"
    graphs_dir.mkdir(exist_ok=True)
    names = iter(graph_names)
    original_show = pyplot.show
    counter = 0

    def save_then_show(*args: Any, **kwargs: Any) -> Any:
        nonlocal counter
        counter += 1
        name = next(names, f"{counter:02d}_additional_graph")
        figure = pyplot.gcf()
        figure.savefig(graphs_dir / f"{name}.png", dpi=160, bbox_inches="tight")
        return original_show(*args, **kwargs)

    pyplot.show = save_then_show
    return graphs_dir
