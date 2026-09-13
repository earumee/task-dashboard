import base64
import binascii
import hmac
import os
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import unquote
from xml.sax.saxutils import escape

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

app = FastAPI(title="Azure WebDAV")

DAV_NS = "DAV:"
USERNAME = os.environ.get("WEBDAV_USERNAME", "")
PASSWORD = os.environ.get("WEBDAV_PASSWORD", "")
CONTAINER = os.environ.get("AZURE_STORAGE_CONTAINER", "webdav")
ACCOUNT_URL = os.environ["AZURE_STORAGE_ACCOUNT_URL"]

credential = DefaultAzureCredential()
blob_service = BlobServiceClient(account_url=ACCOUNT_URL, credential=credential)
container = blob_service.get_container_client(CONTAINER)


def authenticate(authorization: str | None) -> None:
    if not USERNAME or not PASSWORD:
        raise RuntimeError("WEBDAV_USERNAME and WEBDAV_PASSWORD must be configured")
    if not authorization or not authorization.lower().startswith("basic "):
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": 'Basic realm="Azure WebDAV"'},
        )
    try:
        supplied = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
        user, separator, password = supplied.partition(":")
    except (binascii.Error, UnicodeDecodeError):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if (
        not separator
        or not hmac.compare_digest(user, USERNAME)
        or not hmac.compare_digest(password, PASSWORD)
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")


def blob_name(path: str) -> str:
    decoded = unquote(path).replace("\\", "/")
    parts = [part for part in decoded.split("/") if part]
    if any(part in (".", "..") for part in parts):
        raise HTTPException(status_code=400, detail="Invalid path")
    return "/".join(parts)


def dav_path(name: str, directory: bool = False) -> str:
    return "/" + name + ("/" if directory and name else "")


def propstat(path: str, is_collection: bool, size: int = 0, modified=None) -> str:
    resource_type = "<D:collection/>" if is_collection else ""
    length = "0" if is_collection else str(size)
    last_modified = format_datetime(
        modified or datetime.now(timezone.utc), usegmt=True
    )
    return (
        "<D:response>"
        f"<D:href>{escape(path)}</D:href>"
        "<D:propstat><D:prop>"
        f"<D:resourcetype>{resource_type}</D:resourcetype>"
        f"<D:getcontentlength>{length}</D:getcontentlength>"
        f"<D:getlastmodified>{escape(last_modified)}</D:getlastmodified>"
        "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
        "</D:response>"
    )


def multistatus(responses: list[str]) -> Response:
    body = (
        '<?xml version="1.0" encoding="utf-8" ?>'
        '<D:multistatus xmlns:D="DAV:">'
        + "".join(responses)
        + "</D:multistatus>"
    )
    return Response(content=body, status_code=207, media_type="application/xml")


def find_directory(name: str) -> bool:
    prefix = name.rstrip("/") + "/" if name else ""
    return next(container.list_blobs(name_starts_with=prefix), None) is not None


@app.api_route(
    "/{path:path}",
    methods=["OPTIONS", "PROPFIND", "GET", "PUT", "DELETE", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"],
)
async def webdav(
    request: Request,
    path: str = "",
    authorization: str | None = Header(default=None),
    depth: str | None = Header(default="0"),
    destination: str | None = Header(default=None),
):
    authenticate(authorization)
    name = blob_name(path)
    method = request.method

    if method == "OPTIONS":
        return Response(
            status_code=200,
            headers={
                "Allow": "OPTIONS, PROPFIND, GET, PUT, DELETE, MKCOL, COPY, MOVE, LOCK, UNLOCK",
                "DAV": "1,2",
                "MS-Author-Via": "DAV",
            },
        )

    if method == "PROPFIND":
        is_dir = not name or find_directory(name)
        if not is_dir:
            try:
                properties = container.get_blob_client(name).get_blob_properties()
            except ResourceNotFoundError:
                raise HTTPException(status_code=404, detail="Resource not found")
            return multistatus(
                [propstat(dav_path(name), False, properties.size, properties.last_modified)]
            )
        responses = [propstat(dav_path(name, True), True)]
        if depth != "0":
            prefix = name.rstrip("/") + "/" if name else ""
            for item in container.list_blobs(name_starts_with=prefix):
                child = item.name[len(prefix) :].split("/", 1)[0]
                child_name = prefix + child
                is_child_dir = "/" in item.name[len(prefix) :]
                if child_name != item.name and not is_child_dir:
                    continue
                if not any(r.endswith(escape(dav_path(child_name, is_child_dir)) + "</D:href>") for r in responses):
                    responses.append(propstat(dav_path(child_name, is_child_dir), is_child_dir, item.size, item.last_modified))
        return multistatus(responses)

    if method == "GET":
        if not name:
            raise HTTPException(status_code=400, detail="A file path is required")
        try:
            downloader = container.get_blob_client(name).download_blob()
        except ResourceNotFoundError:
            raise HTTPException(status_code=404, detail="Resource not found")
        return StreamingResponse(downloader.chunks(), media_type="application/octet-stream")

    if method == "PUT":
        if not name:
            raise HTTPException(status_code=400, detail="A file path is required")
        data = await request.body()
        container.upload_blob(
            name,
            data,
            overwrite=True,
            content_settings=ContentSettings(content_type=request.headers.get("content-type")),
        )
        return Response(status_code=201)

    if method == "DELETE":
        if name and find_directory(name):
            for item in container.list_blobs(name_starts_with=name.rstrip("/") + "/"):
                container.delete_blob(item.name)
        elif name:
            try:
                container.delete_blob(name)
            except ResourceNotFoundError:
                raise HTTPException(status_code=404, detail="Resource not found")
        return Response(status_code=204)

    if method == "MKCOL":
        if not name:
            raise HTTPException(status_code=405, detail="Root already exists")
        try:
            container.upload_blob(name.rstrip("/") + "/.directory", b"", overwrite=False)
        except ResourceExistsError:
            raise HTTPException(status_code=405, detail="Collection already exists")
        return Response(status_code=201)

    if method in ("COPY", "MOVE"):
        if not destination:
            raise HTTPException(status_code=400, detail="Destination header is required")
        destination_name = blob_name(destination.split("://", 1)[-1].split("/", 3)[-1])
        source = container.get_blob_client(name)
        target = container.get_blob_client(destination_name)
        try:
            target.start_copy_from_url(source.url)
            if method == "MOVE":
                source.delete_blob()
        except ResourceNotFoundError:
            raise HTTPException(status_code=404, detail="Resource not found")
        return Response(status_code=201)

    if method == "LOCK":
        return Response(
            content='<?xml version="1.0" encoding="utf-8"?><D:prop xmlns:D="DAV:"><D:lockdiscovery/></D:prop>',
            status_code=200,
            media_type="application/xml",
            headers={"Lock-Token": "<opaquelocktoken:azure-webdav>"},
        )

    return Response(status_code=204)
