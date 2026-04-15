"""Job 5 — review 생성 진입점 (content-generator-engineer 담당)."""
from __future__ import annotations

import click


@click.command("generate-reviews")
@click.option("--spot-id", "spot_id", required=True)
@click.option("--dataset-version", "dataset_version", default="v1")
def generate_reviews_command(spot_id: str, dataset_version: str) -> None:
    """ContentSpec.activity_result → review × 2 후보 (스텁)."""
    click.echo(f"[stub] generate_reviews spot_id={spot_id} dataset_version={dataset_version}")
    # TODO(content-generator-engineer): rating 분포 + review_text 생성 구현.


if __name__ == "__main__":
    generate_reviews_command()
