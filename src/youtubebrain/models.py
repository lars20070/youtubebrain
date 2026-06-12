"""Pydantic models for YouTube Takeout data."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class Subtitle(BaseModel):
    """Channel reference attached to a watch-history entry."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: HttpUrl


# @lat: [[takeout#WatchedVideo model]]
class WatchedVideo(BaseModel):
    """One record from the Google Takeout watch-history.json array.

    Snake_case Python fields are aliased to the camelCase JSON keys produced by
    Google Takeout. title_url and subtitles are optional because deleted or
    orphaned videos lack one or both.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    header: str
    title: str
    title_url: HttpUrl | None = Field(default=None, alias="titleUrl")
    subtitles: list[Subtitle] = Field(default_factory=list)
    time: datetime
    products: list[str]
    activity_controls: list[str] = Field(alias="activityControls")
    description: str | None = None
