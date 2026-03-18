"""Typed models for the modular tasgi example app."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppInfoOut:
    service: str
    version: str
    docs_url: str
    routers: list[str]


@dataclass
class TaskCreateIn:
    title: str
    owner: str


@dataclass
class TaskOut:
    task_id: str
    title: str
    owner: str
    status: str


@dataclass
class QueueStatsOut:
    total_tasks: int
    completed_tasks: int
    pending_tasks: int

