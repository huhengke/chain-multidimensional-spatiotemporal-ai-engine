"""Pydantic request schemas for runtime-profile administration."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProfileCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    display_name: str
    environment: str
    storage: dict[str, Any]
    catalog: dict[str, Any]
    semantics: dict[str, Any] = Field(default_factory=dict)
    topology: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=2, max_length=500)


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    display_name: str | None = None
    environment: str | None = None
    storage: dict[str, Any] | None = None
    catalog: dict[str, Any] | None = None
    semantics: dict[str, Any] | None = None
    topology: dict[str, Any] | None = None
    reason: str = Field(min_length=2, max_length=500)


class ProfileVersionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=500)


class ProfileActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    confirm_profile_id: str
    reason: str = Field(min_length=2, max_length=500)
