"""Indexing throughput benchmark.

Plan target (tech-lead-plan.md §7): index a 10k-line repo in < 60s.

Marked ``slow`` — excluded from default ``pytest`` run. Execute with:
    pytest -m slow tests/integration/test_perf_indexing.py -s
"""
from __future__ import annotations

import textwrap
import time
from pathlib import Path

import pytest

from codepal.config import AppConfig
from codepal.db.chroma import make_ephemeral_client
from codepal.indexer.pipeline import IndexerPipeline


class _FakeEmbedder:
    async def embed(self, text: str) -> list[float]:
        return [0.1] * 16

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 16 for _ in texts]

    async def close(self) -> None:
        return None


def _generate_repo(root: Path, total_lines: int = 10_000) -> int:
    """Create a synthetic Python repo with roughly *total_lines* lines."""
    lines_per_file = 100
    n_files = total_lines // lines_per_file
    template = textwrap.dedent(
        """
        def func_{i}(x):
            '''Function number {i}.'''
            y = x + {i}
            if y > {i}:
                return y * 2
            return y - 1
        """
    ).strip() + "\n"

    written = 0
    for fi in range(n_files):
        body = "\n".join(template.format(i=fi * 10 + j) for j in range(10))
        (root / f"mod_{fi}.py").write_text(body)
        written += body.count("\n")
    return written


@pytest.mark.slow
@pytest.mark.asyncio
async def test_index_10k_line_repo_under_60s(tmp_path: Path) -> None:
    project = tmp_path / "bigrepo"
    project.mkdir()
    line_count = _generate_repo(project, total_lines=10_000)
    assert line_count >= 9_000, f"only wrote {line_count} lines"

    cfg = AppConfig()
    cfg.indexer.state_db = str(tmp_path / "state.db")
    chroma = make_ephemeral_client()
    embedder = _FakeEmbedder()
    pipeline = IndexerPipeline(chroma=chroma, embedder=embedder, cfg=cfg.indexer)  # type: ignore[arg-type]
    await pipeline.init()

    t0 = time.perf_counter()
    result = await pipeline.index_path(project, "bigrepo")
    elapsed = time.perf_counter() - t0

    print(
        f"\n[perf] indexed {result['indexed']} chunks "
        f"from ~{line_count} lines in {elapsed:.2f}s "
        f"({result['indexed'] / max(elapsed, 1e-3):.0f} chunks/s)"
    )
    assert result["errors"] == []
    assert result["indexed"] > 0
    assert elapsed < 60.0, f"indexing took {elapsed:.1f}s (target <60s)"
