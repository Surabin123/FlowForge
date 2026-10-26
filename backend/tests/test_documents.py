import asyncio
import io
from pathlib import Path
from starlette.datastructures import UploadFile
from app import config
from app.database import init_db
from app.main import upload_document, document_text, download_document, delete_document
from fastapi import HTTPException

def test_document_upload_text_and_original_download(tmp_path,monkeypatch):
    monkeypatch.setattr(config.settings,"database",str(tmp_path/"test.db"))
    monkeypatch.setattr(config.settings,"documents",str(tmp_path/"docs"))
    config.settings.document_path.mkdir(parents=True,exist_ok=True);init_db()
    uploaded=asyncio.run(upload_document(UploadFile(file=io.BytesIO(b"revision on monday"),filename="notes.txt",headers=None)))
    did=uploaded["id"]
    assert document_text(did)["text"]=="revision on monday"
    response=download_document(did)
    assert Path(response.path).read_bytes()==b"revision on monday"
    assert delete_document(did)=={"deleted":True}
    try: document_text(did)
    except HTTPException as exc: assert exc.status_code==404
    else: raise AssertionError("Deleted document remained available")

def test_document_type_is_restricted(tmp_path,monkeypatch):
    monkeypatch.setattr(config.settings,"database",str(tmp_path/"test.db"))
    monkeypatch.setattr(config.settings,"documents",str(tmp_path/"docs"))
    config.settings.document_path.mkdir(parents=True,exist_ok=True);init_db()
    try:
        asyncio.run(upload_document(UploadFile(file=io.BytesIO(b"x"),filename="app.exe",headers=None)))
    except HTTPException as exc: assert exc.status_code==415
    else: raise AssertionError("Unsupported document type was accepted")

def test_failed_text_extraction_does_not_expose_parser_details(tmp_path,monkeypatch):
    monkeypatch.setattr(config.settings,"database",str(tmp_path/"test.db"))
    monkeypatch.setattr(config.settings,"documents",str(tmp_path/"docs"))
    config.settings.document_path.mkdir(parents=True,exist_ok=True);init_db()
    uploaded=asyncio.run(upload_document(UploadFile(file=io.BytesIO(b"\xff"),filename="broken.txt",headers=None)))
    assert uploaded["status"]=="failed"
    assert uploaded["error"]=="Text extraction failed; the file may be invalid or unsupported"
    from app.main import retry_document_processing
    try:
        retry_document_processing(uploaded["id"])
    except HTTPException as exc:
        assert exc.status_code==422
        assert exc.detail=="Document text extraction failed"
    else:
        raise AssertionError("Invalid document unexpectedly parsed")
