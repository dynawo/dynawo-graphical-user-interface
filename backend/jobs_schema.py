#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Reads a Dynawo jobs.xsd into a tree of elements and attributes, plus its lxml validator.

XSD constructs not handled raise JobsSchemaError.
"""

import functools
import os
from dataclasses import dataclass, field

from lxml import etree

_XS = "http://www.w3.org/2001/XMLSchema"

_SUPPORTED_TAGS = {
    "schema", "element", "complexType", "sequence", "all", "attribute",
    "simpleType", "restriction", "enumeration", "unique", "selector", "field",
    "annotation", "documentation", "appinfo",
}

# XSD built-in type -> field type.
_BUILTIN_TYPES = {
    "string": "string", "normalizedString": "string", "token": "string",
    "NMTOKEN": "string", "Name": "string", "NCName": "string", "anyURI": "string",
    "float": "number", "double": "number", "decimal": "number",
    "int": "integer", "integer": "integer", "long": "integer", "short": "integer",
    "nonNegativeInteger": "integer", "positiveInteger": "integer",
    "unsignedInt": "integer",
    "boolean": "boolean",
}


class JobsSchemaError(Exception):
    """jobs.xsd is missing, unreadable or not supported."""


@dataclass(frozen=True)
class AttributeSpec:
    name: str
    type: str  # string, number, integer, boolean or enum

    required: bool = False
    default: str | None = None
    values: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        d = {"name": self.name, "type": self.type, "required": self.required}
        if self.default is not None:
            d["default"] = self.default
        if self.values:
            d["values"] = list(self.values)
        return d


@dataclass(frozen=True)
class UniqueSpec:
    """`attribute` must be unique among the `element` children."""
    element: str
    attribute: str

    def to_dict(self) -> dict:
        return {"element": self.element, "attribute": self.attribute}


@dataclass(frozen=True)
class ElementSpec:
    name: str
    min: int = 1
    max: int | None = 1  # None: unbounded
    ordered: bool = True  # False for xs:all
    attributes: tuple[AttributeSpec, ...] = ()
    children: tuple["ElementSpec", ...] = ()
    unique: tuple[UniqueSpec, ...] = ()

    def child(self, name: str) -> "ElementSpec | None":
        return next((c for c in self.children if c.name == name), None)

    def attribute(self, name: str) -> AttributeSpec | None:
        return next((a for a in self.attributes if a.name == name), None)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "min": self.min,
            "max": self.max,
            "ordered": self.ordered,
            "attributes": [a.to_dict() for a in self.attributes],
            "children": [c.to_dict() for c in self.children],
            "unique": [u.to_dict() for u in self.unique],
        }


@dataclass(frozen=True)
class JobsSchema:
    xsd_path: str
    root: ElementSpec
    validator: etree.XMLSchema = field(compare=False, repr=False)

    def find(self, path: str) -> ElementSpec | None:
        """Element at a path such as "jobs/job/modeler", or None."""
        names = path.strip("/").split("/")
        if not names or names[0] != self.root.name:
            return None
        spec: ElementSpec | None = self.root
        for name in names[1:]:
            spec = spec.child(name) if spec else None
        return spec

    def to_dict(self) -> dict:
        return {"xsd": self.xsd_path, "root": self.root.to_dict()}


def jobs_xsd_path(dynawo_executable: str) -> str:
    """Path of jobs.xsd in the Dynawo distribution of this executable."""
    return os.path.join(os.path.dirname(os.path.abspath(dynawo_executable)), "share", "xsd", "jobs.xsd")


def load_jobs_schema(xsd_path: str) -> JobsSchema:
    """Load jobs.xsd, cached on path and modification time."""
    try:
        mtime = os.stat(xsd_path).st_mtime_ns
    except FileNotFoundError:
        raise JobsSchemaError(f"No jobs.xsd found at {xsd_path}") from None
    except OSError as exc:
        raise JobsSchemaError(f"Cannot read {xsd_path}: {exc}") from None
    return _load_cached(xsd_path, mtime)


def schema_for_executable(dynawo_executable: str | None) -> tuple[JobsSchema | None, str | None]:
    """(schema, error) for an executable; (None, None) without one. Never raises."""
    if not dynawo_executable:
        return None, None
    try:
        return load_jobs_schema(jobs_xsd_path(dynawo_executable)), None
    except JobsSchemaError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"Cannot load {jobs_xsd_path(dynawo_executable)}: {exc}"


@functools.lru_cache(maxsize=8)
def _load_cached(xsd_path: str, _mtime: int) -> JobsSchema:
    try:
        doc = etree.parse(xsd_path)
    except (OSError, etree.XMLSyntaxError) as exc:
        raise JobsSchemaError(f"{xsd_path} is not a readable XML file: {exc}") from None
    try:
        validator = etree.XMLSchema(doc)
    except etree.XMLSchemaParseError as exc:
        raise JobsSchemaError(f"{xsd_path} is not a valid XSD: {exc}") from None
    try:
        root = _SchemaReader(doc.getroot()).read()
    except JobsSchemaError as exc:
        raise JobsSchemaError(f"{xsd_path}: {exc}") from None
    return JobsSchema(xsd_path=xsd_path, root=root, validator=validator)


def _xs(tag: str) -> str:
    return f"{{{_XS}}}{tag}"


def _occurs(value: str | None) -> int | None:
    if value is None:
        return 1
    return None if value == "unbounded" else int(value)


class _SchemaReader:
    """Builds the ElementSpec tree of an XSD document."""

    def __init__(self, schema: etree._Element) -> None:
        for el in schema.iter():
            if not isinstance(el.tag, str):  # comments, processing instructions
                continue
            if el.tag.startswith(f"{{{_XS}}}"):
                local = etree.QName(el).localname
                if local not in _SUPPORTED_TAGS:
                    raise JobsSchemaError(f"unsupported XSD construct <xs:{local}> (line {el.sourceline})")
        self._schema = schema
        self._target_ns = schema.get("targetNamespace")
        self._complex = {t.get("name"): t for t in schema.findall(_xs("complexType"))}
        self._simple = {t.get("name"): t for t in schema.findall(_xs("simpleType"))}

    def read(self) -> ElementSpec:
        roots = self._schema.findall(_xs("element"))
        if len(roots) != 1:
            raise JobsSchemaError(f"expected one top-level element, found {len(roots)}")
        return self._element(roots[0], min_occurs=1, max_occurs=1, types_in_use=())

    def _resolve(self, el: etree._Element, qname: str) -> tuple[str | None, str]:
        """(namespace, local name) of a type reference."""
        prefix, _, local = qname.rpartition(":")
        ns = el.nsmap.get(prefix or None)
        if prefix and ns is None:
            raise JobsSchemaError(f"unknown prefix in type {qname!r} (line {el.sourceline})")
        return ns, local

    def _simple_type(self, el: etree._Element, type_ref: str | None,
                     inline: etree._Element | None) -> tuple[str, tuple[str, ...]]:
        """(field type, enumeration values) of an attribute type."""
        if inline is not None:
            return self._restriction(inline)
        if type_ref is None:
            return "string", ()
        ns, local = self._resolve(el, type_ref)
        if ns == _XS:
            if local not in _BUILTIN_TYPES:
                raise JobsSchemaError(f"unsupported built-in type xs:{local} (line {el.sourceline})")
            return _BUILTIN_TYPES[local], ()
        if ns == self._target_ns and local in self._simple:
            return self._restriction(self._simple[local])
        raise JobsSchemaError(f"unknown simple type {type_ref!r} (line {el.sourceline})")

    def _restriction(self, simple_type: etree._Element) -> tuple[str, tuple[str, ...]]:
        restriction = simple_type.find(_xs("restriction"))
        if restriction is None:
            raise JobsSchemaError(f"simple type without xs:restriction (line {simple_type.sourceline})")
        values = tuple(e.get("value") for e in restriction.findall(_xs("enumeration")))
        base, _ = self._simple_type(restriction, restriction.get("base"), restriction.find(_xs("simpleType")))
        return ("enum", values) if values else (base, ())

    def _element(self, el: etree._Element, min_occurs: int, max_occurs: int | None,
                 types_in_use: tuple[str, ...]) -> ElementSpec:
        if el.get("ref") is not None:
            raise JobsSchemaError(f"element references (ref=) are not supported (line {el.sourceline})")
        name = el.get("name")
        type_ref = el.get("type")
        complex_type = el.find(_xs("complexType"))

        if complex_type is None and type_ref is not None:
            ns, local = self._resolve(el, type_ref)
            if ns == self._target_ns and local in self._complex:
                if local in types_in_use:
                    raise JobsSchemaError(f"recursive type {local!r} (line {el.sourceline})")
                complex_type = self._complex[local]
                types_in_use = types_in_use + (local,)
        if complex_type is None:
            raise JobsSchemaError(f"element {name!r} has no complex type (line {el.sourceline})")

        attributes = []
        for attr in complex_type.findall(_xs("attribute")):
            if attr.get("use") == "prohibited":
                continue
            if attr.get("ref") is not None:
                raise JobsSchemaError(f"attribute references (ref=) are not supported (line {attr.sourceline})")
            form_type, values = self._simple_type(attr, attr.get("type"), attr.find(_xs("simpleType")))
            attributes.append(AttributeSpec(
                name=attr.get("name"),
                type=form_type,
                required=attr.get("use") == "required",
                default=attr.get("default", attr.get("fixed")),
                values=values,
            ))

        groups = complex_type.findall(_xs("sequence")) + complex_type.findall(_xs("all"))
        if len(groups) > 1:
            raise JobsSchemaError(f"complex type with several content groups (line {complex_type.sourceline})")
        children: list[ElementSpec] = []
        ordered = True
        if groups:
            group = groups[0]
            ordered = etree.QName(group).localname == "sequence"
            # The group's minOccurs is ignored: children keep their own occurrence.
            if _occurs(group.get("maxOccurs")) != 1:
                raise JobsSchemaError(f"repeated content group (line {group.sourceline})")
            for child in group:
                if not isinstance(child.tag, str) or child.tag == _xs("annotation"):
                    continue
                if child.tag != _xs("element"):
                    raise JobsSchemaError(
                        f"nested <xs:{etree.QName(child).localname}> in a content group (line {child.sourceline})")
                children.append(self._element(
                    child,
                    min_occurs=_occurs(child.get("minOccurs")),
                    max_occurs=_occurs(child.get("maxOccurs")),
                    types_in_use=types_in_use,
                ))

        return ElementSpec(
            name=name,
            min=min_occurs,
            max=max_occurs,
            ordered=ordered,
            attributes=tuple(attributes),
            children=tuple(children),
            unique=tuple(self._unique(u) for u in el.findall(_xs("unique"))),
        )

    def _unique(self, unique: etree._Element) -> UniqueSpec:
        selector = unique.find(_xs("selector"))
        fld = unique.findall(_xs("field"))
        xpath = selector.get("xpath", "").strip() if selector is not None else ""
        field_xpath = fld[0].get("xpath", "").strip() if len(fld) == 1 else ""
        # Only "prefix:child" selectors and "@attribute" fields.
        if not xpath or any(c in xpath for c in "/|[@") or not field_xpath.startswith("@") \
                or any(c in field_xpath[1:] for c in "/|[@"):
            raise JobsSchemaError(f"unsupported xs:unique (line {unique.sourceline})")
        return UniqueSpec(element=xpath.rpartition(":")[2], attribute=field_xpath[1:].rpartition(":")[2])
