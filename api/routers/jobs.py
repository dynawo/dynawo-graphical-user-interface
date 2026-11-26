#
# Copyright (c) 2026, RTE (http://www.rte-france.com)
# See AUTHORS.txt
# All rights reserved.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, you can obtain one at http://mozilla.org/MPL/2.0/.
# SPDX-License-Identifier: MPL-2.0
#

"""Endpoints to read, check and save .jobs files against the selected Dynawo's schema."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.dependencies import get_session
from api.session_store import UserSession
from backend.jobs_document import (
    DYN_NS,
    FILE_REFS,
    JobsConflictError,
    JobsDocumentError,
    SessionContext,
    check,
    file_token,
    parse,
    render,
    write,
)
from backend.jobs_hints import load_hints, new_element
from backend.jobs_schema import JobsSchema
from backend.par_parser import parse_par

router = APIRouter(tags=["jobs"])


def _schema(session: UserSession) -> JobsSchema:
    if not session.dynawo_executable:
        raise HTTPException(status_code=400, detail="No Dynawo selected: choose one in the Dynawo Version page first")
    if session.jobs_schema is None:
        raise HTTPException(status_code=400, detail=session.jobs_schema_error or "jobs.xsd not loaded")
    return session.jobs_schema


def _jobs_path(session: UserSession, jobs_file: str) -> str:
    info = session.session_manager.files.get(jobs_file)
    if info is None or info.ftype != "jobs":
        raise HTTPException(status_code=404, detail=f"{jobs_file} is not a jobs file of the session")
    return session.session_manager.get_path(jobs_file)


def _context(session: UserSession, jobs_file: str | None) -> SessionContext | None:
    if not jobs_file:
        return None
    sm = session.session_manager
    return SessionContext(sm.working_dir, jobs_file, {name: info.ftype for name, info in sm.files.items()})


def _register(session: UserSession, jobs_file: str, created: bool = False) -> None:
    """Refresh the session's records of a written file; a created file's content becomes its original."""
    sm = session.session_manager
    info = sm.register_existing_file(jobs_file) if created else sm.refresh_info(jobs_file)
    session.uploaded_files_info.setdefault(jobs_file, {}).update(size=info.size, ftype=info.ftype)


def _modified(session: UserSession, jobs_file: str, content: bytes) -> bool:
    original = session.session_manager.get_raw(jobs_file)
    return original is not None and original != content


def _parse_error(exc: JobsDocumentError) -> HTTPException:
    return HTTPException(status_code=422, detail={"message": str(exc), "line": exc.line, "column": exc.column})


def _check_root(document: dict) -> None:
    root = document.get("root") or {}
    if root.get("tag") != "jobs" or root.get("ns", DYN_NS) != DYN_NS:
        raise HTTPException(status_code=422, detail="The root element must be <jobs> in Dynawo's namespace")


class DocumentRequest(BaseModel):
    document: dict
    jobs_file: str | None = None


class TextRequest(BaseModel):
    text: str
    jobs_file: str | None = None


class SaveRequest(BaseModel):
    jobs_file: str
    document: dict
    token: str


class CreateRequest(BaseModel):
    jobs_file: str
    document: dict


@router.get("/schema")
def get_schema(session: UserSession = Depends(get_session)):
    """Schema of the selected Dynawo, the attributes that name files, and the hints."""
    schema = _schema(session)
    return {
        "dynawo": session.dynawo_executable,
        **schema.to_dict(),
        "file_refs": [{"element": e, "attribute": a, "ftype": t} for (e, a), t in FILE_REFS.items()],
        "hints": load_hints(),
    }


@router.get("/new")
def get_new(path: str, session: UserSession = Depends(get_session)):
    """New element at a schema path, e.g. "jobs/job" or "jobs/job/outputs/timeline"."""
    spec = _schema(session).find(path)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"{path} is not in the schema")
    return {"node": new_element(spec)}


@router.get("/par-sets/{par_file:path}")
def get_par_sets(par_file: str, session: UserSession = Depends(get_session)):
    """Set ids of a .par file of the session."""
    info = session.session_manager.files.get(par_file)
    if info is None or info.ftype != "par":
        raise HTTPException(status_code=404, detail=f"{par_file} is not a .par file of the session")
    with open(session.session_manager.get_path(par_file), "rb") as fh:
        return list(parse_par(fh.read()))


@router.get("/file/{jobs_file:path}")
def get_file(jobs_file: str, session: UserSession = Depends(get_session)):
    """Document and token of a jobs file; its raw text and the error when it does not parse."""
    path = _jobs_path(session, jobs_file)
    with open(path, "rb") as fh:
        content = fh.read()
    token = file_token(content)
    modified = _modified(session, jobs_file, content)
    try:
        return {"document": parse(content), "token": token, "modified": modified}
    except JobsDocumentError as exc:
        return {
            "document": None,
            "token": token,
            "modified": modified,
            "text": content.decode("utf-8", errors="replace"),
            "error": {"message": str(exc), "line": exc.line, "column": exc.column},
        }


@router.post("/check")
def post_check(req: DocumentRequest, session: UserSession = Depends(get_session)):
    """Normalised document and its issues."""
    try:
        document, issues = check(req.document, _schema(session), _context(session, req.jobs_file))
    except JobsDocumentError as exc:
        raise _parse_error(exc)
    return {"document": document, "issues": issues}


@router.post("/render")
def post_render(req: DocumentRequest, session: UserSession = Depends(get_session)):
    """XML text of a document, as it would be saved."""
    try:
        return {"text": render(req.document, _schema(session))}
    except JobsDocumentError as exc:
        raise _parse_error(exc)


@router.post("/parse")
def post_parse(req: TextRequest, session: UserSession = Depends(get_session)):
    """Document and issues of an XML text; 422 with line and column when it does not parse."""
    schema = _schema(session)
    try:
        document, issues = check(parse(req.text), schema, _context(session, req.jobs_file))
    except JobsDocumentError as exc:
        raise _parse_error(exc)
    return {"document": document, "issues": issues}


@router.put("/file")
def put_file(req: SaveRequest, session: UserSession = Depends(get_session)):
    """Save a jobs file; 409 if it changed since it was read."""
    schema = _schema(session)
    path = _jobs_path(session, req.jobs_file)
    _check_root(req.document)
    try:
        document, token, issues = write(path, req.document, schema, req.token, _context(session, req.jobs_file))
    except JobsConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except JobsDocumentError as exc:
        raise _parse_error(exc)
    _register(session, req.jobs_file)
    with open(path, "rb") as fh:
        modified = _modified(session, req.jobs_file, fh.read())
    return {"document": document, "token": token, "issues": issues, "modified": modified}


@router.post("/restore/{jobs_file:path}")
def restore_file(jobs_file: str, session: UserSession = Depends(get_session)):
    """Write back the content the file had when it entered the session."""
    path = _jobs_path(session, jobs_file)
    original = session.session_manager.get_raw(jobs_file)
    if original is None:
        raise HTTPException(status_code=404, detail=f"No original content for {jobs_file}")
    with open(path, "wb") as fh:
        fh.write(original)
    _register(session, jobs_file)
    return {"ok": True}


@router.post("/file")
def create_file(req: CreateRequest, session: UserSession = Depends(get_session)):
    """Create a jobs file in the session; 409 if the name is taken."""
    schema = _schema(session)
    if not req.jobs_file.endswith(".jobs"):
        raise HTTPException(status_code=400, detail="The file name must end with .jobs")
    try:
        path = session.session_manager.get_path(req.jobs_file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if session.session_manager.has_file(req.jobs_file):
        raise HTTPException(status_code=409, detail=f"{req.jobs_file} already exists")
    _check_root(req.document)
    try:
        document, token, issues = write(path, req.document, schema, None, _context(session, req.jobs_file))
    except JobsConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except JobsDocumentError as exc:
        raise _parse_error(exc)
    _register(session, req.jobs_file, created=True)
    return {"document": document, "token": token, "issues": issues}
