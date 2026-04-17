"""Pydantic models matching the SonarQube API JSON responses."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class TextRange(BaseModel):
    start_line: int = Field(alias="startLine")
    end_line: int = Field(alias="endLine")
    start_offset: int = Field(0, alias="startOffset")
    end_offset: int = Field(0, alias="endOffset")

    model_config = {"populate_by_name": True}


class FlowLocation(BaseModel):
    component: str = ""
    text_range: Optional[TextRange] = Field(None, alias="textRange")
    msg: str = ""

    model_config = {"populate_by_name": True}


class Flow(BaseModel):
    locations: list[FlowLocation] = []


class Issue(BaseModel):
    key: str
    rule: str
    severity: str  # BLOCKER, CRITICAL, MAJOR, MINOR, INFO
    component: str
    project: str = ""
    line: Optional[int] = None
    text_range: Optional[TextRange] = Field(None, alias="textRange")
    flows: list[Flow] = []
    status: str = "OPEN"
    message: str = ""
    effort: Optional[str] = None
    debt: Optional[str] = None
    author: Optional[str] = None
    tags: list[str] = []
    type: str = ""  # BUG, VULNERABILITY, CODE_SMELL, SECURITY_HOTSPOT
    creation_date: Optional[str] = Field(None, alias="creationDate")
    update_date: Optional[str] = Field(None, alias="updateDate")

    model_config = {"populate_by_name": True}

    @property
    def file_path(self) -> str:
        """Extract relative file path from the component key (project:path)."""
        if ":" in self.component:
            return self.component.rsplit(":", 1)[1]
        return self.component

    @property
    def start_line(self) -> Optional[int]:
        if self.line is not None:
            return self.line
        if self.text_range is not None:
            return self.text_range.start_line
        return None

    @property
    def end_line(self) -> Optional[int]:
        if self.text_range is not None:
            return self.text_range.end_line
        if self.line is not None:
            return self.line
        return None


class Rule(BaseModel):
    key: str
    name: str = ""
    html_desc: Optional[str] = Field(None, alias="htmlDesc")
    severity: str = ""
    type: str = ""
    lang: Optional[str] = None
    lang_name: Optional[str] = Field(None, alias="langName")

    model_config = {"populate_by_name": True}


class Component(BaseModel):
    key: str
    path: Optional[str] = None
    name: Optional[str] = None
    qualifier: Optional[str] = None
    language: Optional[str] = None


class Paging(BaseModel):
    page_index: int = Field(1, alias="pageIndex")
    page_size: int = Field(100, alias="pageSize")
    total: int = 0

    model_config = {"populate_by_name": True}


class IssuesSearchResponse(BaseModel):
    paging: Paging = Paging()
    issues: list[Issue] = []
    components: list[Component] = []
    rules: list[Rule] = []


class RuleShowResponse(BaseModel):
    rule: Rule


class Branch(BaseModel):
    name: str
    is_main: bool = Field(False, alias="isMain")
    type: str = ""
    status: Optional[dict] = None

    model_config = {"populate_by_name": True}


class PullRequest(BaseModel):
    key: str
    title: str = ""
    branch: str = ""
    base: str = ""
    status: Optional[dict] = None
