"""Job 1 — content_spec_builder 진입점 (pipeline-infra-architect 담당).

Phase Peer-D 에서 ``--mode`` 옵션이 추가되었다. default 는 ``peer``.
legacy event_log (``event_log_legacy_v1.jsonl``) 를 읽어야 할 때만
``--mode legacy`` 를 준다.
"""
from __future__ import annotations

import click

from pipeline.spec.builder import build_content_spec as _build


@click.command("build-content-spec")
@click.option(
    "--event-log",
    "event_log",
    default="../spot-simulator/output/event_log.jsonl",
    show_default=True,
    help="event_log.jsonl 경로 (cwd 기준 상대).",
)
@click.option("--spot-id", "spot_id", required=True, help="빌드할 spot id (예: S_0001).")
@click.option(
    "--region-features",
    "region_features",
    default=None,
    help="region_features.json 경로 (생략 시 spot-simulator 기본 경로).",
)
@click.option(
    "--skills-catalog",
    "skills_catalog",
    default=None,
    help="skills_catalog.yaml 경로 (peer mode 한정, fee_breakdown 추정용).",
)
@click.option(
    "--mode",
    "mode",
    type=click.Choice(["peer", "legacy"], case_sensitive=False),
    default="peer",
    show_default=True,
    help="'peer' = Phase Peer-D 이후 포맷, 'legacy' = Phase 1 CREATE_SPOT 포맷.",
)
def build_content_spec_command(
    event_log: str,
    spot_id: str,
    region_features: str | None,
    skills_catalog: str | None,
    mode: str,
) -> None:
    """단일 spot 의 ContentSpec 을 stdout 으로 출력."""
    spec = _build(
        event_log,
        spot_id,
        mode=mode.lower(),
        region_features_path=region_features,
        skills_catalog_path=skills_catalog,
    )
    click.echo(spec.model_dump_json(indent=2))


if __name__ == "__main__":
    build_content_spec_command()
