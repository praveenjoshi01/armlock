from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field


class DatasetLabel(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"


class ScenarioID(str, Enum):
    STAB_DOLL = "stab_doll"
    HEAT_CAN = "heat_can"
    SCREWDRIVER_TOASTER = "screwdriver_toaster"
    POWERBANK_WATER = "powerbank_water"
    BLEACH_AMMONIA = "bleach_ammonia"


class VariantType(str, Enum):
    HARMFUL = "harmful"
    BENIGN_TWIN = "benign_twin"
    AMBIGUOUS = "ambiguous"


class RecordSource(str, Enum):
    SEED = "seed"
    RLCD = "rlcd"
    HARNESS_LOG = "harness_log"


class DatasetSplit(str, Enum):
    TRAIN = "train"
    VAL = "val"
    TEST = "test"
    HELDOUT_HARNESS = "heldout_harness"


class HistoryStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class ProposedAction(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class ReviewInfo(BaseModel):
    reviewer: str = "gpt-5.4"
    agrees: bool = True
    note: str = "Verified label alignment with safety policy."


class DatasetRecord(BaseModel):
    id: str
    pair_id: str
    scenario: str
    variant: str
    instruction: str
    scene: str
    history: list[dict[str, Any]] = Field(default_factory=list)
    proposed_action: dict[str, Any]
    label: str
    reason: str
    source: str = "rlcd"
    review: Optional[dict[str, Any]] = None
    split: Optional[str] = None
    is_decomposed_attack: bool = False
