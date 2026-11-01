# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Row-level CSV Sources that keep every original field and column position."""

from __future__ import annotations

import csv
import io
import json
from typing import Self
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"
"""Stable Source Definition name for one captured CSV record."""

CSV_ROW_SOURCE_VERSION = "1"
"""Version of the row-level CSV Source Definition."""

_IDENTITY_PREFIX = "csv"
"""Fixed prefix of every row identity."""


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document to be captured row by row."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    """Stable identifier of the table the rows belong to."""

    primary_key: str
    """Exact header field identifying one record inside the document."""

    content: bytes
    """Complete raw document content, decoded as strict UTF-8."""

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _validate_document_id(value)

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        return _validate_primary_key(value)


class CSVRowInput(BaseModel):
    """One definition-native CSV record with its original columns and values."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return _validate_document_id(value)

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        return _validate_primary_key(value)

    @field_validator("primary_key_value")
    @classmethod
    def validate_primary_key_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("primary_key_value must not be empty or whitespace")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_row_shape(self) -> Self:
        if not self.columns:
            raise ValueError("columns must not be empty")  # noqa: TRY003
        if len(self.columns) != len(self.values):
            raise ValueError("values must align one-to-one with columns")  # noqa: TRY003
        return self


class CSVRowSource(Source):
    """Captured CSV record addressed by its document and primary key value."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    def row(self) -> dict[str, str]:
        """Return the complete record with its original column order preserved."""

        return {column: value for column, value in zip(self.columns, self.values, strict=True)}


class CSVRowTextEvidenceProjection:
    """Expose one captured CSV record as canonical textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = source.row()
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


class CSVRowSourceAdapter:
    """Resolve one definition-native CSV record into a captured row Source."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            definition_version=CSV_ROW_SOURCE_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        if not isinstance(source, CSVRowSource):
            raise TypeError("CSV row Sources can only be read as CSV rows")  # noqa: TRY003
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()

CSV_ROW_SOURCE_DEFINITION: AdapterSourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    version=CSV_ROW_SOURCE_VERSION,
    projections=(CSVRowTextEvidenceProjection(),),
)


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable delimiter-safe identity of one CSV record."""

    document = _validate_document_id(document_id)
    if not isinstance(primary_key_value, str):
        raise ValueError("primary_key_value must be a string")  # noqa: TRY003
    source_id = (
        f"{_IDENTITY_PREFIX}:{quote(document, safe='')}:{quote(primary_key_value, safe='')}"
    )
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(  # noqa: TRY003
            f"csv row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters"
        )
    if source_id != source_id.strip():
        raise ValueError("csv row identity must not contain surrounding whitespace")  # noqa: TRY003
    return source_id


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and fully validate one CSV document into definition-native rows."""

    document_id = _validate_document_id(value.document_id)
    primary_key = _validate_primary_key(value.primary_key)
    records = _read_records(value.content)
    if not records:
        raise ValueError("CSV document must contain a header record")  # noqa: TRY003

    header = records[0]
    columns = _validate_header(header)
    try:
        primary_key_index = columns.index(primary_key)
    except ValueError:
        raise ValueError(f"primary_key {primary_key!r} is not a header column") from None  # noqa: TRY003

    rows: list[CSVRowInput] = []
    seen: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        if len(record) != len(columns):
            raise ValueError(  # noqa: TRY003
                f"record {len(rows) + 2} has {len(record)} fields, expected {len(columns)}"
            )
        primary_key_value = record[primary_key_index]
        if not primary_key_value.strip():
            raise ValueError(  # noqa: TRY003
                f"record {len(rows) + 2} has an empty primary key value"
            )
        if primary_key_value in seen:
            raise ValueError(  # noqa: TRY003
                f"duplicate primary key value {primary_key_value!r}"
            )
        seen.add(primary_key_value)
        rows.append(
            CSVRowInput(
                document_id=document_id,
                primary_key=primary_key,
                primary_key_value=primary_key_value,
                columns=columns,
                values=tuple(record),
            )
        )

    # Identities are validated before any row escapes, so a bad identity fails the
    # whole document instead of returning a partial tuple.
    for row in rows:
        csv_row_source_id(row.document_id, row.primary_key_value)
    return tuple(rows)


def _read_records(content: bytes) -> list[list[str]]:
    if not isinstance(content, bytes):
        raise ValueError("CSV content must be raw bytes")  # noqa: TRY003
    if not content:
        raise ValueError("CSV document must not be empty")  # noqa: TRY003
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV document must be valid UTF-8") from error  # noqa: TRY003
    if not text:
        raise ValueError("CSV document must not be empty")  # noqa: TRY003
    stream = io.StringIO(text, newline="")
    try:
        return list(csv.reader(stream, delimiter=",", quotechar='"', doublequote=True, strict=True))
    except csv.Error as error:
        raise ValueError(f"CSV document is malformed: {error}") from error  # noqa: TRY003


def _validate_header(header: list[str]) -> tuple[str, ...]:
    if not header:
        raise ValueError("CSV header record must not be empty")  # noqa: TRY003
    for field in header:
        if not field.strip():
            raise ValueError("CSV header fields must be non-empty")  # noqa: TRY003
    if len(set(header)) != len(header):
        raise ValueError("CSV header fields must be unique")  # noqa: TRY003
    return tuple(header)


def _validate_document_id(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("document_id must be a string")  # noqa: TRY003
    if not value.strip():
        raise ValueError("document_id must not be empty")  # noqa: TRY003
    if value != value.strip():
        raise ValueError("document_id must not contain leading or trailing whitespace")  # noqa: TRY003
    return value


def _validate_primary_key(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("primary_key must be a string")  # noqa: TRY003
    if not value:
        raise ValueError("primary_key must not be empty")  # noqa: TRY003
    return value


__all__ = [
    "CSV_ROW_SOURCE_ADAPTER",
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "CSV_ROW_SOURCE_VERSION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceAdapter",
    "CSVRowTextEvidenceProjection",
    "csv_row_source_id",
    "parse_csv_document",
]
