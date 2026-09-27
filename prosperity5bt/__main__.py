from __future__ import annotations

import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Annotated, Optional

from typer import Argument, Option, Typer

from prosperity5bt.api import BacktestError, backtest
from prosperity5bt.models import BacktestResult, TradeMatchingMode
from prosperity5bt.open import open_visualizer


def parse_out(out: Optional[Path], no_out: bool) -> Optional[Path]:
    if out is not None:
        return out

    if no_out:
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return Path.cwd() / "backtests" / f"{timestamp}.log"


def print_day_summary(result: BacktestResult) -> None:
    for product, profit in result.product_profits.items():
        print(f"{product}: {profit:,.0f}")

    print(f"Total profit: {result.profit:,.0f}")


def print_overall_summary(results: list[BacktestResult]) -> None:
    print("Profit summary:")

    for result in results:
        print(f"Day {result.day_num}: {result.profit:,.0f}")

    print(f"Total profit: {sum(result.profit for result in results):,.0f}")


def format_path(path: Path) -> str:
    cwd = Path.cwd()
    if path.is_relative_to(cwd):
        return str(path.relative_to(cwd))
    else:
        return str(path)


def version_callback(value: bool) -> None:
    if value:
        print(f"prosperity5bt {metadata.version(__package__)}")
        sys.exit(0)


app = Typer(context_settings={"help_option_names": ["--help", "-h"]})


@app.command()
def cli(
    algorithm: Annotated[Path, Argument(help="Path to the Python file containing the algorithm to backtest.", show_default=False, exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    data_set: Annotated[int, Argument(metavar="DATA_SET", help="The data set to take the round from (3 = Prosperity 3 data, 4 = Prosperity 4 data).", show_default=False)],
    round_num: Annotated[int, Argument(metavar="ROUND", help="The round to backtest.", show_default=False)],
    day: Annotated[Optional[int], Option("--day", help="Backtest only this day instead of every day in the round.", show_default=False)] = None,
    merge_pnl: Annotated[bool, Option("--merge-pnl", help="Merge profit and loss across days.")] = False,
    vis: Annotated[bool, Option("--vis", help="Open backtest results in https://jmerle.github.io/imc-prosperity-3-visualizer/ when done.")] = False,
    out: Annotated[Optional[Path], Option(help="File to save output log to (defaults to backtests/<timestamp>.log).", show_default=False, dir_okay=False, resolve_path=True)] = None,
    no_out: Annotated[bool, Option("--no-out", help="Skip saving output log.")] = False,
    data: Annotated[Optional[Path], Option(help="Path to data directory. Must look similar in structure to prosperity5bt/resources, i.e. contain resources-<n>/round<n>/ subdirectories.", show_default=False, exists=True, file_okay=False, dir_okay=True, resolve_path=True)] = None,
    print_output: Annotated[bool, Option("--print", help="Print the trader's output to stdout while it's running.")] = False,
    match_trades: Annotated[TradeMatchingMode, Option(help="How to match orders against market trades. 'all' matches trades with prices equal to or worse than your quotes, 'worse' matches trades with prices worse than your quotes, 'none' does not match trades against orders at all.")] = TradeMatchingMode.all,
    no_progress: Annotated[bool, Option("--no-progress", help="Don't show progress bars.")] = False,
    original_timestamps: Annotated[bool, Option("--original-timestamps", help="Preserve original timestamps in output log rather than making them increase across days.")] = False,
    version: Annotated[bool, Option("--version", "-v", help="Show the program's version number and exit.", is_eager=True, callback=version_callback)] = False,
) -> None:  # fmt: skip
    if out is not None and no_out:
        print("Error: --out and --no-out are mutually exclusive")
        sys.exit(1)

    output_file = parse_out(out, no_out)
    days_started = 0

    def on_day_start(day_num: int) -> None:
        nonlocal days_started

        if days_started > 0:
            print()
        days_started += 1

        print(f"Backtesting {algorithm} on data set {data_set} round {round_num} day {day_num}")

    try:
        output = backtest(
            algorithm,
            data_set,
            round_num,
            day,
            data=data,
            match_trades=match_trades,
            print_output=print_output,
            show_progress_bars=not no_progress and not print_output,
            merge_pnl=merge_pnl,
            original_timestamps=original_timestamps,
            on_day_start=on_day_start,
            on_day_complete=print_day_summary,
        )
    except BacktestError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if len(output.results) > 1:
        print()
        print_overall_summary(output.results)

    if output_file is not None:
        output.save(output_file)
        print(f"\nSuccessfully saved backtest results to {format_path(output_file)}")

    if vis and output_file is not None:
        open_visualizer(output_file)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
