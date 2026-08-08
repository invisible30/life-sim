"""7 个 agent + 1 个随机"""
from .base import Agent, AgentView, AgentVote
from .rational import RationalAgent
from .emotional import EmotionalAgent
from .ambitious import AmbitiousAgent
from .realistic import RealisticAgent
from .family import FamilyAgent
from .future_me import FutureMeAgent
from .body import BodyAgent
from .luck import LuckAgent


def make_all_agents(llm, state, enabled: dict | None = None) -> list[Agent]:
    """构造所有 agent"""
    enabled = enabled or {}
    cls_map = {
        "rational": RationalAgent,
        "emotional": EmotionalAgent,
        "ambitious": AmbitiousAgent,
        "realistic": RealisticAgent,
        "family": FamilyAgent,
        "future_me": FutureMeAgent,
        "body": BodyAgent,
        "luck": LuckAgent,
    }
    agents = []
    for name, cls in cls_map.items():
        if enabled.get(name, True):
            agents.append(cls(llm, state))
    return agents


__all__ = [
    "Agent", "AgentView", "AgentVote",
    "RationalAgent", "EmotionalAgent", "AmbitiousAgent", "RealisticAgent",
    "FamilyAgent", "FutureMeAgent", "BodyAgent", "LuckAgent",
    "make_all_agents",
]
