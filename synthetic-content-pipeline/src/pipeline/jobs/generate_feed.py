"""Job 2 — feed preview 생성 진입점 (content-generator-engineer 담당)."""
from __future__ import annotations

import click


@click.command("generate-feed")
@click.option("--spot-id", "spot_id", required=True)
@click.option("--dataset-version", "dataset_version", default="v1")
def generate_feed_command(spot_id: str, dataset_version: str) -> None:
    """ContentSpec → feed preview × 2 후보 (스텁)."""
    click.echo(f"[stub] generate_feed spot_id={spot_id} dataset_version={dataset_version}")
    # TODO(content-generator-engineer): codex_client + jinja2 prompt + DB insert 구현.


if __name__ == "__main__":
    generate_feed_command()
