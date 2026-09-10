"""Dashboard state models and helpers (testable without Streamlit)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from core.ledger import Ledger
from core.portfolio import PortfolioConfig, load_portfolio_config

LaneAState = Literal["HUNT", "LOCKED", "HALT"]
LaneBState = Literal["IDLE", "SCOUT", "DEPLOY", "COOLDOWN", "HALT"]

LANE_A_STATES: tuple[LaneAState, ...] = ("HUNT", "LOCKED", "HALT")
LANE_B_STATES: tuple[LaneBState, ...] = ("IDLE", "SCOUT", "DEPLOY", "COOLDOWN", "HALT")

AGENT_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("orchestrator", "Orchestrator", "Cross-lane coordination"),
    ("market_data", "Market Data", "Quote ingestion"),
    ("lane_a_hunter", "Lane A Hunter", "ORB + VWAP signals"),
    ("lane_a_execution", "Lane A Execution", "Lane A orders"),
    ("lane_b_scout", "Lane B Scout", "Earnings calendar"),
    ("lane_b_deploy", "Lane B Deploy", "Options deployment"),
    ("risk_manager", "Risk Manager", "Exposure / HALT"),
)


@dataclass
class AgentHeartbeat:
    name: str
    label: str
    role: str
    last_seen: float | None = None

    @property
    def is_alive(self) -> bool:
        if self.last_seen is None:
            return False
        return (time.time() - self.last_seen) < 120

    def ping(self) -> None:
        self.last_seen = time.time()


@dataclass
class DashboardState:
    config: PortfolioConfig
    ledger: Ledger
    lane_a_state: LaneAState = "HUNT"
    lane_b_state: LaneBState = "IDLE"
    agents: dict[str, AgentHeartbeat] = field(default_factory=dict)

    @classmethod
    def bootstrap(cls, config_path: str = "config/portfolio.yaml") -> DashboardState:
        config = load_portfolio_config(config_path)
        agents = {
            agent_id: AgentHeartbeat(name=agent_id, label=label, role=role)
            for agent_id, label, role in AGENT_DEFINITIONS
        }
        return cls(
            config=config,
            ledger=config.create_ledger(),
            agents=agents,
        )

    def reset_ledger(self) -> None:
        self.ledger = self.config.create_ledger()

    def lane_allocation_pct(self) -> tuple[float, float]:
        total = self.ledger.total
        if total <= 0:
            return 0.0, 0.0
        return (
            round(self.ledger.lane_a_balance / total * 100, 1),
            round(self.ledger.lane_b_balance / total * 100, 1),
        )

    def ping_all_agents(self) -> None:
        for agent in self.agents.values():
            agent.ping()

    def ping_agent(self, agent_id: str) -> None:
        if agent_id in self.agents:
            self.agents[agent_id].ping()
