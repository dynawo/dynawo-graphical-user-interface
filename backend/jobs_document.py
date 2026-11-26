#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Reads, checks and writes a .jobs file as a JSON tree.

Document: {"nsmap", "prolog", "root", "epilog"}. Node: an element
{"tag", "attrs", "children", optional "ns" and "text"}, a {"comment"} or a
{"pi", "text"}. An issue points to a node by its index path from the root.
"""

import hashlib
import os
import re
import threading
from dataclasses import dataclass, field

from lxml import etree

from backend.jobs_parser import job_relative
from backend.jobs_schema import ElementSpec, JobsSchema
from backend.par_parser import parse_par

DYN_NS = "http://www.rte-france.com/dynawo"

# (element, attribute) naming a file, and the file type expected (None: any).
FILE_REFS: dict[tuple[str, str], str | None] = {
    ("solver", "parFile"): "par",
    ("network", "iidmFile"): "iidm",
    ("network", "parFile"): "par",
    ("dynModels", "dydFile"): "dyd",
    ("curves", "inputFile"): "crv",
    ("localInit", "parFile"): "par",
    ("initialState", "file"): None,
    ("criteria", "criteriaFile"): None,
    ("finalStateValues", "inputFile"): None,
}

# Elements whose parId must be a set of their parFile.
PAR_SET_REFS = {"solver", "network", "localInit"}

# Attribute an lxml validation message is about.
_ATTRIBUTE_IN_MESSAGE = re.compile(r"[Aa]ttribute '([^']+)'")

# Schemas are shared between sessions: one validation at a time.
_validation_lock = threading.Lock()


class JobsDocumentError(Exception):
    """The XML cannot be parsed, or the tree cannot be turned into XML."""

    def __init__(self, message: str, line: int | None = None, column: int | None = None) -> None:
        super().__init__(message)
        self.line = line
        self.column = column


class JobsConflictError(Exception):
    """The file on disk is not the one the edit started from."""


@dataclass
class SessionContext:
    """What the file checks need to know about the session."""
    working_dir: str
    jobs_file: str  # path of the .jobs, relative to working_dir
    file_types: dict[str, str | None] = field(default_factory=dict)  # session file -> detected type


# ── Reading ──────────────────────────────────────────────────────────────────

def file_token(content: bytes) -> str:
    """Version token of a file content."""
    return hashlib.sha256(content).hexdigest()[:16]


def parse(content: bytes | str) -> dict:
    """Document tree of an XML text."""
    if isinstance(content, str):
        content = content.encode("utf-8")
    parser = etree.XMLParser(remove_blank_text=True, resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(content, parser)
    except etree.XMLSyntaxError as exc:
        line, column = exc.position if exc.position else (None, None)
        raise JobsDocumentError(exc.msg, line, column) from None
    return {
        "nsmap": {prefix or "": uri for prefix, uri in root.nsmap.items()},
        "prolog": [_node(n) for n in reversed(list(root.itersiblings(preceding=True))) if _is_misc(n)],
        "root": _node(root),
        "epilog": [_node(n) for n in root.itersiblings() if _is_misc(n)],
    }


def read(path: str) -> tuple[dict, str]:
    """(document tree, version token) of a .jobs file."""
    with open(path, "rb") as fh:
        content = fh.read()
    return parse(content), file_token(content)


def _is_misc(el) -> bool:
    return isinstance(el, (etree._Comment, etree._ProcessingInstruction))


def _node(el) -> dict:
    if isinstance(el, etree._Comment):
        return {"comment": el.text or ""}
    if isinstance(el, etree._ProcessingInstruction):
        return {"pi": el.target, "text": el.text or ""}
    qname = etree.QName(el)
    node: dict = {"tag": qname.localname}
    if qname.namespace != DYN_NS:
        node["ns"] = qname.namespace
    node["attrs"] = dict(el.attrib)
    if el.text and el.text.strip():
        node["text"] = el.text
    node["children"] = [_node(c) for c in el if isinstance(c.tag, str) or _is_misc(c)]
    return node


# ── Ordering ─────────────────────────────────────────────────────────────────

def _is_dyn_element(node: dict) -> bool:
    return "tag" in node and node.get("ns", DYN_NS) == DYN_NS


def normalise(doc: dict, schema: JobsSchema) -> dict:
    """Copy of the document with children put in schema order where it has a sequence."""
    root = doc["root"]
    spec = schema.root if _is_dyn_element(root) and root["tag"] == schema.root.name else None
    return {**doc, "root": _ordered(root, spec)}


def _ordered(node: dict, spec: ElementSpec | None) -> dict:
    children = [
        _ordered(c, spec.child(c["tag"]) if spec and _is_dyn_element(c) else None) if "tag" in c else c
        for c in node.get("children", [])
    ]
    if spec and spec.ordered and spec.children:
        # A comment moves with the element after it; unknown elements go last.
        index = {s.name: i for i, s in enumerate(spec.children)}
        units, pending = [], []
        for child in children:
            pending.append(child)
            if "tag" in child:
                units.append(pending)
                pending = []
        units.sort(key=lambda u: index.get(u[-1]["tag"], len(index)) if _is_dyn_element(u[-1]) else len(index))
        children = [c for unit in units for c in unit] + pending
    return {**node, "children": children}


# ── Writing ──────────────────────────────────────────────────────────────────

def _build(doc: dict) -> etree._ElementTree:
    nsmap = {(prefix or None): uri for prefix, uri in doc.get("nsmap", {}).items()} or {None: DYN_NS}
    try:
        root = etree.Element(_qname(doc["root"]), nsmap=nsmap)
        _fill(root, doc["root"])
        for item in doc.get("prolog", []):
            root.addprevious(_misc(item))
        for item in reversed(doc.get("epilog", [])):
            root.addnext(_misc(item))
    except (KeyError, TypeError, ValueError) as exc:
        raise JobsDocumentError(f"Invalid document: {exc}") from None
    return etree.ElementTree(root)


def _qname(node: dict) -> str:
    return etree.QName(node.get("ns", DYN_NS), node["tag"]).text


def _misc(node: dict):
    if "comment" in node:
        return etree.Comment(node["comment"])
    if "pi" in node:
        return etree.ProcessingInstruction(node["pi"], node.get("text") or None)
    raise ValueError(f"unknown node {node!r}")


def _fill(el: etree._Element, node: dict) -> None:
    for name, value in node.get("attrs", {}).items():
        el.set(name, str(value))
    if node.get("text"):
        el.text = node["text"]
    for child in node.get("children", []):
        if "tag" in child:
            _fill(etree.SubElement(el, _qname(child)), child)
        else:
            el.append(_misc(child))


def serialise(doc: dict) -> bytes:
    """XML bytes of a document, indented."""
    tree = _build(doc)
    etree.indent(tree, space="  ")
    return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", pretty_print=True)


def render(doc: dict, schema: JobsSchema) -> str:
    """XML text of a document as write() would save it."""
    return serialise(normalise(doc, schema)).decode("utf-8")


def write(path: str, doc: dict, schema: JobsSchema, token: str | None,
          context: SessionContext | None = None) -> tuple[dict, str, list[dict]]:
    """Save a document; token None creates the file. Returns (document, new token, issues).

    Issues never block the save; a token not matching the file raises JobsConflictError."""
    if token is None:
        if os.path.exists(path):
            raise JobsConflictError(f"{os.path.basename(path)} already exists")
    else:
        try:
            with open(path, "rb") as fh:
                current = fh.read()
        except FileNotFoundError:
            raise JobsConflictError(f"{os.path.basename(path)} was deleted") from None
        if file_token(current) != token:
            raise JobsConflictError(f"{os.path.basename(path)} was changed since it was read")
    normalised, issues = check(doc, schema, context)
    content = serialise(normalised)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return normalised, file_token(content), issues


# ── Checks ───────────────────────────────────────────────────────────────────

def check(doc: dict, schema: JobsSchema, context: SessionContext | None = None) -> tuple[dict, list[dict]]:
    """(normalised document, issues): XSD errors, then file warnings if a context is given.

    Issue: {"level": "error" | "warning", "path": [child indexes], "attribute"?, "message"}."""
    normalised = normalise(doc, schema)
    issues = _schema_errors(normalised, schema)
    if context is not None:
        issues += _file_warnings(normalised, context)
    return normalised, issues


def _schema_errors(doc: dict, schema: JobsSchema) -> list[dict]:
    parser = etree.XMLParser(remove_blank_text=True)
    tree = etree.ElementTree(etree.fromstring(serialise(doc), parser))
    with _validation_lock:
        schema.validator.validate(tree)
        errors = list(schema.validator.error_log)
    lines = _paths_by_line(tree.getroot(), doc["root"], [])
    issues = []
    for e in errors:
        issue = {"level": "error", "path": lines.get(e.line, []), "message": e.message.replace(f"{{{DYN_NS}}}", "")}
        attribute = _ATTRIBUTE_IN_MESSAGE.search(e.message)
        if attribute:
            issue["attribute"] = attribute.group(1)
        issues.append(issue)
    return issues


def _paths_by_line(el: etree._Element, node: dict, path: list[int]) -> dict[int, list[int]]:
    """Line of each element -> index path of its node, walking XML and tree together."""
    lines = {el.sourceline: path}
    xml_children = [c for c in el if isinstance(c.tag, str) or _is_misc(c)]
    for i, (child_el, child_node) in enumerate(zip(xml_children, node.get("children", []))):
        if "tag" in child_node:
            lines.update(_paths_by_line(child_el, child_node, path + [i]))
    return lines


def _file_warnings(doc: dict, context: SessionContext) -> list[dict]:
    issues: list[dict] = []
    files = {os.path.normpath(name): ftype for name, ftype in context.file_types.items()}
    par_sets: dict[str, set[str] | None] = {}

    def warn(path, message, attribute=None):
        issue = {"level": "warning", "path": path, "message": message}
        if attribute:
            issue["attribute"] = attribute
        issues.append(issue)

    def session_file(ref: str) -> str | None:
        """Session path of a reference, or None if outside the session."""
        if os.path.isabs(ref):
            return None
        rel = job_relative(context.jobs_file, ref)
        return None if rel == ".." or rel.startswith(".." + os.sep) else rel

    def sets_of(rel: str) -> set[str] | None:
        if rel not in par_sets:
            try:
                with open(os.path.join(context.working_dir, rel), "rb") as fh:
                    par_sets[rel] = set(parse_par(fh.read()))
            except Exception:
                par_sets[rel] = None
        return par_sets[rel]

    def visit(node: dict, path: list[int]) -> None:
        tag, attrs = node["tag"], node.get("attrs", {})
        for (ref_tag, attr), expected in FILE_REFS.items():
            if tag != ref_tag or attr not in attrs:
                continue
            ref = attrs[attr].strip()
            rel = session_file(ref) if ref else None
            if not ref:
                warn(path, "No file given", attr)
            elif rel is None:
                warn(path, f"{ref} is outside the session", attr)
            elif rel not in files:
                warn(path, f"{ref} is not in the session", attr)
            elif expected and files[rel] != expected:
                warn(path, f"{ref} is not a .{expected} file", attr)
            elif tag in PAR_SET_REFS and attr == "parFile" and attrs.get("parId"):
                sets = sets_of(rel)
                if sets is not None and attrs["parId"] not in sets:
                    warn(path, f"Set {attrs['parId']} not found in {ref}", "parId")
        if tag == "simulation":
            try:
                if float(attrs["startTime"]) >= float(attrs["stopTime"]):
                    warn(path, "startTime must be lower than stopTime", "stopTime")
            except (KeyError, ValueError):
                pass  # missing or not a number: reported by the schema
        for i, child in enumerate(node.get("children", [])):
            if _is_dyn_element(child):
                visit(child, path + [i])

    root = doc["root"]
    if _is_dyn_element(root):
        visit(root, [])
        names: set[str] = set()
        for i, job in enumerate(root.get("children", [])):
            if _is_dyn_element(job) and job["tag"] == "job":
                name = job.get("attrs", {}).get("name")
                if name in names:
                    warn([i], f"Another job is named {name}", "name")
                names.add(name)
    return issues
