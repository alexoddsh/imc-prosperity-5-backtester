from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from functools import reduce
from importlib import import_module, reload
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Union

from prosperity5bt.data import has_day_data
from prosperity5bt.datamodel import Symbol
from prosperity5bt.file_reader import FileReader, FileSystemReader, PackageResourcesReader
from prosperity5bt.models import BacktestResult, TradeMatchingMode
from prosperity5bt.runner import run_backtest

# Anything backtest() accepts as an algorithm: a path to a file exposing a Trader class, an already-imported
# module exposing one, a Trader class itself, or a ready-made Trader instance.
Algorithm = Union[str, Path, ModuleType, type, Any]

ACTIVITY_LOG_HEADER = (
    "day;timestamp;product;bid_price_1;bid_volume_1;bid_price_2;bid_volume_2;bid_price_3;bid_volume_3;"
    "ask_price_1;ask_volume_1;ask_price_2;ask_volume_2;ask_price_3;ask_volume_3;mid_price;profit_and_loss"
)

# The days a round is searched for when no specific day is requested.
CANDIDATE_DAYS = range(-5, 100)


class BacktestError(Exception):
    """Raised when a backtest cannot be set up or run."""


def create_file_reader(data: str | Path | None = None) -> FileReader:
    """Creates a reader for the bundled data, or for a custom resources directory when data is given."""
    if data is None:
        return PackageResourcesReader()

    root = Path(data).expanduser().resolve()
    if not root.is_dir():
        raise BacktestError(f"{root} is not a directory")

    return FileSystemReader(root)


def validate_data_set(file_reader: FileReader, data_set: int) -> int:
    """Checks that a data set exists in the data being read, and returns it."""
    available = file_reader.data_sets()

    if len(available) == 0:
        raise BacktestError("no data sets found, expected one or more resources-<n> directories in the data directory")

    if data_set not in available:
        raise BacktestError(f"data set {data_set} not found, available data sets: {', '.join(map(str, available))}")

    return data_set


def resolve_days(file_reader: FileReader, data_set: int, round_num: int, day: int | None = None) -> list[int]:
    """Returns the days of a round to backtest: just `day`, or every day the round has when `day` is None."""
    validate_data_set(file_reader, data_set)

    if day is not None:
        if not has_day_data(file_reader, data_set, round_num, day):
            raise BacktestError(f"no data found for data set {data_set} round {round_num} day {day}")

        return [day]

    days = [
        candidate_day
        for candidate_day in CANDIDATE_DAYS
        if has_day_data(file_reader, data_set, round_num, candidate_day)
    ]

    if len(days) == 0:
        raise BacktestError(f"no data found for data set {data_set} round {round_num}")

    return days


def import_algorithm(path: str | Path) -> ModuleType:
    """Imports a Python file as a module, adding its directory to sys.path so its own imports resolve."""
    file = Path(path).expanduser().resolve()
    if not file.is_file():
        raise BacktestError(f"{file} is not a file")

    directory = str(file.parent)
    if directory not in sys.path:
        sys.path.append(directory)

    try:
        return import_module(file.stem)
    except ModuleNotFoundError as e:
        raise BacktestError(f"{file} is not a valid algorithm file: {e}") from e


def get_trader_class(module: ModuleType, label: str | None = None) -> type:
    trader_class = getattr(module, "Trader", None)
    if trader_class is None:
        raise BacktestError(f"{label or getattr(module, '__name__', module)} does not expose a Trader class")

    return trader_class


def create_trader_factory(algorithm: Algorithm) -> Callable[[], Any]:
    """Turns an algorithm into a callable that produces the trader to use for a single day.

    A path or a class yields a fresh trader per day, matching what the official submission environment does.
    A module yields a fresh trader per day too, but is never reloaded. An instance is reused across days.
    """
    if isinstance(algorithm, (str, Path)):
        label = str(algorithm)
        module = import_algorithm(algorithm)
        get_trader_class(module, label)

        def factory() -> Any:
            reload(module)
            return get_trader_class(module, label)()

        return factory

    if isinstance(algorithm, ModuleType):
        trader_class = get_trader_class(algorithm)
        return trader_class

    if isinstance(algorithm, type):
        if not hasattr(algorithm, "run"):
            raise BacktestError(f"{algorithm.__name__} does not have a run method")

        return algorithm

    if not hasattr(algorithm, "run"):
        raise BacktestError(
            f"{algorithm!r} is not a valid algorithm, "
            "expected a path to a file, a module, a Trader class, or an object with a run method"
        )

    return lambda: algorithm


def format_output(result: BacktestResult) -> str:
    """Renders a result in the format of the official submission environment's output log."""
    return "".join(
        [
            "Sandbox logs:\n",
            *[str(row) for row in result.sandbox_logs],
            "\n\n\nActivities log:\n",
            ACTIVITY_LOG_HEADER,
            "\n",
            "\n".join(map(str, result.activity_logs)),
            "\n\n\n\n\nTrade History:\n",
            "[\n",
            ",\n".join(map(str, result.trades)),
            "]",
        ]
    )


def write_output(output_file: str | Path, result: BacktestResult) -> Path:
    """Writes a result to a log file, creating parent directories as needed."""
    file = Path(output_file)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(format_output(result), encoding="utf-8")

    return file


def merge_results(
    a: BacktestResult, b: BacktestResult, merge_profit_loss: bool, merge_timestamps: bool
) -> BacktestResult:
    """Concatenates two results into one, optionally carrying b's timestamps and profit forward from a."""
    sandbox_logs = a.sandbox_logs[:]
    activity_logs = a.activity_logs[:]
    trades = a.trades[:]

    if merge_timestamps:
        a_last_timestamp = a.activity_logs[-1].timestamp
        timestamp_offset = a_last_timestamp + 100
    else:
        timestamp_offset = 0

    sandbox_logs.extend([row.with_offset(timestamp_offset) for row in b.sandbox_logs])
    trades.extend([row.with_offset(timestamp_offset) for row in b.trades])

    if merge_profit_loss:
        profit_loss_offsets = defaultdict(float, a.product_profits)

        activity_logs.extend(
            [row.with_offset(timestamp_offset, profit_loss_offsets[row.columns[2]]) for row in b.activity_logs]
        )
    else:
        activity_logs.extend([row.with_offset(timestamp_offset, 0) for row in b.activity_logs])

    return BacktestResult(a.data_set, a.round_num, a.day_num, sandbox_logs, activity_logs, trades)


@dataclass
class BacktestOutput:
    """The result of backtesting an algorithm on one round of one data set."""

    data_set: int
    round_num: int
    results: list[BacktestResult] = field(default_factory=list)
    output_file: Path | None = None

    merge_pnl: bool = False
    merge_timestamps: bool = True

    @property
    def days(self) -> list[int]:
        """The days that were backtested, in the order they ran."""
        return [result.day_num for result in self.results]

    @property
    def profit(self) -> float:
        """The total profit and loss across every day that ran."""
        return sum(result.profit for result in self.results)

    @property
    def profit_by_day(self) -> dict[int, float]:
        """The profit and loss per day."""
        return {result.day_num: result.profit for result in self.results}

    @property
    def profit_by_product(self) -> dict[Symbol, float]:
        """The profit and loss per product, summed across every day that ran."""
        totals: dict[Symbol, float] = {}
        for result in self.results:
            for product, profit in result.product_profits.items():
                totals[product] = totals.get(product, 0.0) + profit

        return totals

    @property
    def merged(self) -> BacktestResult:
        """Every day merged into a single result, honoring the merge options the backtest ran with."""
        return reduce(
            lambda a, b: merge_results(a, b, self.merge_pnl, self.merge_timestamps),
            self.results,
        )

    @property
    def log(self) -> str:
        """The merged result rendered as an output log, without writing it anywhere."""
        return format_output(self.merged)

    def save(self, output_file: str | Path) -> Path:
        """Writes the merged result to a log file and returns its path."""
        self.output_file = write_output(output_file, self.merged)
        return self.output_file


def backtest(
    algorithm: Algorithm,
    data_set: int,
    round_num: int,
    day: int | None = None,
    *,
    data: str | Path | None = None,
    file_reader: FileReader | None = None,
    match_trades: TradeMatchingMode | str = TradeMatchingMode.all,
    print_output: bool = False,
    show_progress_bars: bool = False,
    out: str | Path | None = None,
    merge_pnl: bool = False,
    original_timestamps: bool = False,
    on_day_start: Callable[[int], None] | None = None,
    on_day_complete: Callable[[BacktestResult], None] | None = None,
) -> BacktestOutput:
    """Backtests an algorithm on one round of one data set.

    Args:
        algorithm: A path to a file exposing a Trader class, a module exposing one, a Trader class, or a Trader
            instance. Paths and classes get a fresh trader per day; an instance is reused across days.
        data_set: The data set to take the round from, e.g. 3 for Prosperity 3 data or 4 for Prosperity 4 data.
        round_num: The round to backtest.
        day: The single day to backtest. Every day the round has is backtested when this is None.
        data: A custom resources directory, laid out with resources-<n>/round<n>/ subdirectories. Defaults to
            the data bundled with this package.
        file_reader: A reader to take data from, instead of `data`. Mutually exclusive with it.
        match_trades: How to match orders against market trades.
        print_output: Let the trader's stdout through to this process's stdout instead of capturing it.
        show_progress_bars: Render a tqdm progress bar per day.
        out: Write the merged output log here. Nothing is written when this is None.
        merge_pnl: Carry profit and loss across day boundaries when merging.
        original_timestamps: Keep each day's own timestamps when merging, rather than making them increase.
        on_day_start: Called with the day number before each day runs.
        on_day_complete: Called with each day's result as it finishes.

    Returns:
        A BacktestOutput holding the per-day results, the profit breakdowns, and the merged output log.

    Raises:
        BacktestError: If the algorithm, the data set, or the requested round/day can't be resolved.
    """
    if file_reader is not None and data is not None:
        raise BacktestError("data and file_reader are mutually exclusive")

    if file_reader is None:
        file_reader = create_file_reader(data)

    trader_factory = create_trader_factory(algorithm)
    days = resolve_days(file_reader, data_set, round_num, day)

    results = []
    for day_num in days:
        if on_day_start is not None:
            on_day_start(day_num)

        result = run_backtest(
            trader_factory(),
            file_reader,
            data_set,
            round_num,
            day_num,
            print_output,
            TradeMatchingMode(match_trades),
            True,
            show_progress_bars,
        )

        results.append(result)

        if on_day_complete is not None:
            on_day_complete(result)

    output = BacktestOutput(
        data_set=data_set,
        round_num=round_num,
        results=results,
        merge_pnl=merge_pnl,
        merge_timestamps=not original_timestamps,
    )

    if out is not None:
        output.save(out)

    return output
