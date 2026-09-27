from dataclasses import dataclass
from enum import Enum
from typing import Any

import orjson

from prosperity5bt.datamodel import Symbol, Trade


@dataclass
class SandboxLogRow:
    timestamp: int
    sandbox_log: str
    lambda_log: str

    def with_offset(self, timestamp_offset: int) -> "SandboxLogRow":
        return SandboxLogRow(
            self.timestamp + timestamp_offset,
            self.sandbox_log,
            self.lambda_log.replace(f"[[{self.timestamp},", f"[[{self.timestamp + timestamp_offset},"),
        )

    def __str__(self) -> str:
        return orjson.dumps(
            {
                "sandboxLog": self.sandbox_log,
                "lambdaLog": self.lambda_log,
                "timestamp": self.timestamp,
            },
            option=orjson.OPT_APPEND_NEWLINE | orjson.OPT_INDENT_2,
        ).decode("utf-8")


@dataclass
class ActivityLogRow:
    columns: list[Any]

    @property
    def timestamp(self) -> int:
        return self.columns[1]

    def with_offset(self, timestamp_offset: int, profit_loss_offset: float) -> "ActivityLogRow":
        new_columns = self.columns[:]
        new_columns[1] += timestamp_offset
        new_columns[-1] += profit_loss_offset

        return ActivityLogRow(new_columns)

    def __str__(self) -> str:
        return ";".join(map(str, self.columns))


@dataclass
class TradeRow:
    trade: Trade
    currency: str = "SEASHELLS"

    @property
    def timestamp(self) -> int:
        return self.trade.timestamp

    def with_offset(self, timestamp_offset: int) -> "TradeRow":
        return TradeRow(
            Trade(
                self.trade.symbol,
                self.trade.price,
                self.trade.quantity,
                self.trade.buyer,
                self.trade.seller,
                self.trade.timestamp + timestamp_offset,
            ),
            self.currency,
        )

    def __str__(self) -> str:
        return (
            "  "
            + f"""
  {{
    "timestamp": {self.trade.timestamp},
    "buyer": "{self.trade.buyer}",
    "seller": "{self.trade.seller}",
    "symbol": "{self.trade.symbol}",
    "currency": "{self.currency}",
    "price": {self.trade.price},
    "quantity": {self.trade.quantity},
  }}
        """.strip()
        )


@dataclass
class BacktestResult:
    data_set: int
    round_num: int
    day_num: int

    sandbox_logs: list[SandboxLogRow]
    activity_logs: list[ActivityLogRow]
    trades: list[TradeRow]

    @property
    def product_profits(self) -> dict[Symbol, float]:
        """The final profit and loss per product, in the order the products appear in the activity logs."""
        if len(self.activity_logs) == 0:
            return {}

        last_timestamp = self.activity_logs[-1].timestamp

        profits: dict[Symbol, float] = {}
        for row in reversed(self.activity_logs):
            if row.timestamp != last_timestamp:
                break

            profits[row.columns[2]] = row.columns[-1]

        return dict(reversed(list(profits.items())))

    @property
    def profit(self) -> float:
        """The final profit and loss summed over all products."""
        return sum(self.product_profits.values())


@dataclass
class MarketTrade:
    trade: Trade
    buy_quantity: int
    sell_quantity: int


class TradeMatchingMode(str, Enum):
    all = "all"
    worse = "worse"
    none = "none"
