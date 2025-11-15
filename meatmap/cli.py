"""
Command line interface wiring together all meatmap pipeline stages.
"""
from __future__ import annotations

from pathlib import Path
from typing import List

import click
from dotenv import load_dotenv

from . import export, merge, scoring
from .models import RawStoreRecord
from .sources import HotPepperClient


def load_environment() -> None:
    cwd_env = Path(".env")
    if cwd_env.exists():
        load_dotenv(dotenv_path=cwd_env, override=False)
        return
    package_env = Path(__file__).resolve().parent.parent / ".env"
    if package_env.exists():
        load_dotenv(dotenv_path=package_env, override=False)
    else:
        load_dotenv()


@click.command()
@click.option("--output", type=click.Path(dir_okay=False), default="output/meatmap.csv", show_default=True)
@click.option("--skip-hotpepper", is_flag=True, help="Skip querying HotPepper.")
@click.option("--include-rank-b", is_flag=True, help="Include rank B stores in the export.")
@click.option("--include-rank-c", is_flag=True, help="Include rank C stores in the export.")
def main(
    output: str,
    skip_hotpepper: bool,
    include_rank_b: bool,
    include_rank_c: bool,
) -> None:
    """
    Run the ingest -> merge -> score -> export pipeline.
    """
    load_environment()
    raw_records: List[RawStoreRecord] = []
    if skip_hotpepper:
        click.echo("HotPepper ingestion skipped via flag. No other sources enabled.", err=True)
    else:
        try:
            click.echo("Fetching from HotPepper…")
            hotpepper_client = HotPepperClient()
            hp_records = hotpepper_client.fetch_tokyo_meat_shops()
            raw_records.extend(hp_records)
            click.echo(f" HotPepper records: {len(hp_records)}")
        except (ValueError, RuntimeError) as exc:
            click.echo(f"Skipping HotPepper: {exc}", err=True)
    if not raw_records:
        raise click.ClickException("No records collected. Enable at least one data source.")
    merged_records = merge.merge_records(raw_records)
    scored_records = scoring.score_records(merged_records)
    include_ranks = ["S", "A"]
    if include_rank_b:
        include_ranks.append("B")
    if include_rank_c:
        include_ranks.append("C")
    output_path = export.export_to_csv(scored_records, Path(output), include_ranks=include_ranks)
    click.echo(f"Exported {output_path} with {len(scored_records)} stores (before filtering).")


if __name__ == "__main__":
    main()
