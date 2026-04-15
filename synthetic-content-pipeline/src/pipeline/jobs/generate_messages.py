"""Job 4 — 커뮤니케이션 snippet 4종 생성 진입점 (content-generator-engineer 담당)."""
from __future__ import annotations

import click


@click.command("generate-messages")
@click.option("--spot-id", "spot_id", required=True)
@click.option("--dataset-version", "dataset_version", default="v1")
def generate_messages_command(spot_id: str, dataset_version: str) -> None:
    """ContentSpec + lifecycle → 메시지 4종 × 2 후보 (스텁)."""
    click.echo(f"[stub] generate_messages spot_id={spot_id} dataset_version={dataset_version}")
    # TODO(content-generator-engineer): 메시지 타입별 generator 구현.


if __name__ == "__main__":
    generate_messages_command()
