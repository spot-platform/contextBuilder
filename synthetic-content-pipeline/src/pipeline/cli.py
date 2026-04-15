"""click group 진입점 — 10개 sub-command 등록."""
from __future__ import annotations

import click

from pipeline.jobs.build_content_spec import build_content_spec_command
from pipeline.jobs.evaluate_critic import evaluate_critic_command
from pipeline.jobs.generate_detail import generate_detail_command
from pipeline.jobs.generate_feed import generate_feed_command
from pipeline.jobs.generate_messages import generate_messages_command
from pipeline.jobs.generate_reviews import generate_reviews_command
from pipeline.jobs.publish import publish_command
from pipeline.jobs.score_and_approve import score_and_approve_command
from pipeline.jobs.validate_cross_reference import validate_cross_reference_command
from pipeline.jobs.validate_individual import validate_individual_command


@click.group()
def cli() -> None:
    """synthetic-content-pipeline CLI 진입점."""


cli.add_command(build_content_spec_command)
cli.add_command(generate_feed_command)
cli.add_command(generate_detail_command)
cli.add_command(generate_messages_command)
cli.add_command(generate_reviews_command)
cli.add_command(validate_individual_command)
cli.add_command(validate_cross_reference_command)
cli.add_command(evaluate_critic_command)
cli.add_command(score_and_approve_command)
cli.add_command(publish_command)


if __name__ == "__main__":
    cli()
