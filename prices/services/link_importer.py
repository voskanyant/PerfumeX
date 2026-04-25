from __future__ import annotations

from dataclasses import dataclass
from email.message import Message
import ipaddress
import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, build_opener

from prices import models


SUPPORTED_EXTENSIONS = (".csv", ".xls", ".xlsx")
URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
MAX_DOWNLOAD_BYTES = 25_000_000
USER_AGENT = "PerfumeX-PriceImporter/1.0"


class LinkImportError(RuntimeError):
    pass


@dataclass
class DownloadedPriceFile:
    filename: str
    payload: bytes
    content_type: str
    source_url: str
    provider: str


def extract_links_from_email(message: Message) -> list[str]:
    texts: list[str] = []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        content_type = (part.get_content_type() or "").lower()
        if content_type not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            texts.append(payload.decode(charset, errors="ignore"))
        except LookupError:
            texts.append(payload.decode("utf-8", errors="ignore"))
    links: list[str] = []
    seen = set()
    for text in texts:
        for raw in URL_RE.findall(text):
            url = raw.rstrip(").,;]")
            if url not in seen:
                seen.add(url)
                links.append(url)
    return links


def detect_provider(url: str) -> str:
    host = urlparse(url).hostname or ""
    host = host.lower()
    if host.endswith("disk.yandex.ru") or host.endswith("yadi.sk"):
        return models.PriceSourceProvider.YANDEX_DISK
    if host.endswith("drive.google.com") or host.endswith("docs.google.com"):
        return models.PriceSourceProvider.GOOGLE_DRIVE
    if host.endswith("cloud.mail.ru"):
        return models.PriceSourceProvider.MAILRU_CLOUD
    return models.PriceSourceProvider.DIRECT_URL


def download_price_source(source: models.SupplierPriceSource, url: str | None = None) -> DownloadedPriceFile:
    source_url = (url or source.url or "").strip()
    if not source_url:
        raise LinkImportError("No source link is configured.")
    provider = source.provider
    if provider == models.PriceSourceProvider.AUTO:
        provider = detect_provider(source_url)
    if provider == models.PriceSourceProvider.YANDEX_DISK:
        return _download_yandex_disk(source_url, source)
    if provider == models.PriceSourceProvider.GOOGLE_DRIVE:
        return _download_google_drive(source_url)
    if provider == models.PriceSourceProvider.MAILRU_CLOUD:
        raise LinkImportError("Mail.ru Cloud public links are not supported yet.")
    return _download_direct(source_url, provider=models.PriceSourceProvider.DIRECT_URL)


def source_matches_email(
    source: models.SupplierPriceSource,
    *,
    from_addr: str,
    subject: str,
    links: list[str],
) -> list[str]:
    supplier = source.supplier
    if not _match_pattern(from_addr, supplier.from_address_pattern):
        return []
    if not _match_pattern(subject, supplier.price_subject_pattern):
        return []
    matched = []
    for link in links:
        if source.url_pattern and source.url_pattern.lower() not in link.lower():
            continue
        provider = source.provider
        detected = detect_provider(link)
        if provider != models.PriceSourceProvider.AUTO and provider != detected:
            continue
        matched.append(link)
    return matched


def _match_pattern(value: str, pattern: str) -> bool:
    if not pattern:
        return True
    return pattern.lower() in (value or "").lower()


def _is_supported_filename(filename: str) -> bool:
    return filename.lower().endswith(SUPPORTED_EXTENSIONS)


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else ""
    return name or "downloaded_price.xlsx"


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise LinkImportError("Only http/https links are allowed.")
    host = parsed.hostname
    if not host:
        raise LinkImportError("Link host is missing.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise LinkImportError(f"Could not resolve link host: {host}") from exc
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise LinkImportError("Private/internal download links are not allowed.")


def _http_get(url: str, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> tuple[bytes, str, str, str]:
    _validate_public_url(url)
    opener = build_opener()
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=35) as response:
            content_type = response.headers.get("Content-Type", "")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise LinkImportError("Downloaded file is too large.")
            chunks = []
            total = 0
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise LinkImportError("Downloaded file is too large.")
                chunks.append(chunk)
            filename = _filename_from_content_disposition(
                response.headers.get("Content-Disposition", "")
            )
            return b"".join(chunks), content_type, filename, response.geturl()
    except HTTPError as exc:
        raise LinkImportError(f"Download failed with HTTP {exc.code}.") from exc
    except URLError as exc:
        raise LinkImportError(f"Download failed: {exc.reason}") from exc


def _filename_from_content_disposition(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"filename\*=UTF-8''([^;]+)", value, re.IGNORECASE)
    if match:
        from urllib.parse import unquote

        return unquote(match.group(1)).strip('"')
    match = re.search(r'filename="?([^";]+)"?', value, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _download_direct(url: str, *, provider: str) -> DownloadedPriceFile:
    payload, content_type, header_filename, final_url = _http_get(url)
    filename = header_filename or _filename_from_url(final_url)
    if not _is_supported_filename(filename):
        raise LinkImportError(f"Downloaded file is not a supported spreadsheet: {filename}")
    return DownloadedPriceFile(
        filename=filename,
        payload=payload,
        content_type=content_type,
        source_url=final_url,
        provider=provider,
    )


def _download_yandex_disk(url: str, source: models.SupplierPriceSource) -> DownloadedPriceFile:
    resource = _yandex_api("public/resources", {"public_key": url, "limit": 100})
    items = resource.get("_embedded", {}).get("items") if isinstance(resource, dict) else None
    if items:
        item = _pick_yandex_item(items, source)
        if not item:
            raise LinkImportError("Yandex Disk folder has no matching spreadsheet.")
        return _download_yandex_file(url, item.get("path") or "", item.get("name") or "")
    if resource.get("type") == "file":
        return _download_yandex_file(url, "", resource.get("name") or "")
    raise LinkImportError("Yandex Disk link did not expose a downloadable file.")


def _yandex_api(endpoint: str, params: dict[str, object]) -> dict:
    api_url = f"https://cloud-api.yandex.net/v1/disk/{endpoint}?{urlencode(params)}"
    payload, _, _, _ = _http_get(api_url, max_bytes=5_000_000)
    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise LinkImportError("Yandex Disk API returned invalid JSON.") from exc


def _pick_yandex_item(items: list[dict], source: models.SupplierPriceSource) -> dict | None:
    candidates = []
    pattern = (source.file_pattern or "").lower().strip()
    for item in items:
        if item.get("type") != "file":
            continue
        name = item.get("name") or ""
        if not _is_supported_filename(name):
            continue
        if pattern and pattern not in name.lower():
            continue
        candidates.append(item)
    if not candidates:
        return None
    if source.pick_rule == models.PriceSourcePickRule.FIRST_VALID:
        return candidates[0]
    return sorted(
        candidates,
        key=lambda item: item.get("modified") or item.get("created") or "",
        reverse=True,
    )[0]


def _download_yandex_file(public_key: str, path: str, filename: str) -> DownloadedPriceFile:
    params = {"public_key": public_key}
    if path:
        params["path"] = path
    download_meta = _yandex_api("public/resources/download", params)
    href = download_meta.get("href")
    if not href:
        raise LinkImportError("Yandex Disk did not return a download URL.")
    payload, content_type, header_filename, final_url = _http_get(href)
    resolved_filename = header_filename or filename or _filename_from_url(final_url)
    if not _is_supported_filename(resolved_filename):
        raise LinkImportError(f"Yandex Disk file is not a supported spreadsheet: {resolved_filename}")
    return DownloadedPriceFile(
        filename=resolved_filename,
        payload=payload,
        content_type=content_type,
        source_url=public_key,
        provider=models.PriceSourceProvider.YANDEX_DISK,
    )


def _download_google_drive(url: str) -> DownloadedPriceFile:
    file_id = _google_file_id(url)
    if not file_id:
        raise LinkImportError("Could not detect Google Drive file id.")
    parsed = urlparse(url)
    if "docs.google.com" in (parsed.hostname or "") and "/spreadsheets/" in parsed.path:
        download_url = f"https://docs.google.com/spreadsheets/d/{quote(file_id)}/export?format=xlsx"
        filename = f"google-sheet-{file_id}.xlsx"
    else:
        download_url = f"https://drive.google.com/uc?export=download&id={quote(file_id)}"
        filename = f"google-drive-{file_id}.xlsx"
    payload, content_type, header_filename, final_url = _http_get(download_url)
    resolved_filename = header_filename or filename
    if not _is_supported_filename(resolved_filename):
        resolved_filename = filename
    return DownloadedPriceFile(
        filename=resolved_filename,
        payload=payload,
        content_type=content_type,
        source_url=final_url,
        provider=models.PriceSourceProvider.GOOGLE_DRIVE,
    )


def _google_file_id(url: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if "d" in parts:
        index = parts.index("d")
        if len(parts) > index + 1:
            return parts[index + 1]
    query_id = parse_qs(parsed.query).get("id")
    return query_id[0] if query_id else ""
