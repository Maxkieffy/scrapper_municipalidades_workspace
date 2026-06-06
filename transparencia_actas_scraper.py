from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


PORTAL_BASE_URL = "https://www.portaltransparencia.cl/PortalPdT/directorio-de-organismos-regulados/"
DEFAULT_WORK_ROOT = Path("D:/trabajo max")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_YEARS = (2024, 2025)
MONTHS = (
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)
ALLOWED_EXTENSIONS = {".pdf", ".doc", ".docx", ".zip", ".rar"}
DATE_PATTERNS = (
    re.compile(r"(?P<year>20\d{2})[-_/\.](?P<month>\d{1,2})[-_/\.](?P<day>\d{1,2})"),
    re.compile(r"(?P<day>\d{1,2})[-_/\.](?P<month>\d{1,2})[-_/\.](?P<year>20\d{2})"),
    re.compile(r"(?P<day>\d{2})(?P<month>\d{2})(?P<year>20\d{2})"),
)
FILENAME_CLEAN_RE = re.compile(r"[^A-Za-z0-9._-]+")
URL_RE = re.compile(r"https?://[^\s\"'>]+", re.IGNORECASE)
HREF_RE = re.compile(r"href=(https?://[^\"'\s>]+)", re.IGNORECASE)


@dataclass
class MunicipalityTarget:
    municipalidad: str
    codigo_portal: str


@dataclass
class ActaRecord:
    municipalidad: str
    codigo_portal: str
    anio: int
    fecha_acta: str
    descripcion: str
    url_descarga: str
    ruta_archivo: str
    fuente_portal: str


class PortalScraperError(RuntimeError):
    pass


def find_firefox_binary() -> Optional[Path]:
    env_path = os.environ.get("FIREFOX_BINARY")
    candidates = [
        Path(env_path) if env_path else None,
        Path("C:/Program Files/Mozilla Firefox/firefox.exe"),
        Path("C:/Program Files (x86)/Mozilla Firefox/firefox.exe"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


def download_with_progress(session: requests.Session, url: str, destination: Path) -> None:
    with session.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with destination.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 64):
                if chunk:
                    fh.write(chunk)


def ensure_local_geckodriver(session: requests.Session, tools_root: Path) -> Path:
    driver_dir = tools_root / "drivers" / "geckodriver-win64"
    driver_path = driver_dir / "geckodriver.exe"
    if driver_path.exists():
        return driver_path

    driver_dir.mkdir(parents=True, exist_ok=True)
    release_api = "https://api.github.com/repos/mozilla/geckodriver/releases/latest"
    print("[INFO] Descargando geckodriver automaticamente...", flush=True)
    try:
        response = session.get(release_api, timeout=60)
        response.raise_for_status()
        release = response.json()
    except Exception as exc:
        raise PortalScraperError(
            "No se pudo consultar la ultima version de geckodriver en GitHub. "
            "Revisa la conexion a Internet, proxy o firewall."
        ) from exc

    asset_url = None
    for asset in release.get("assets", []):
        if asset.get("name") == "geckodriver-v0.36.0-win64.zip":
            asset_url = asset.get("browser_download_url")
            break
    if not asset_url:
        for asset in release.get("assets", []):
            name = asset.get("name", "")
            if name.endswith("win64.zip"):
                asset_url = asset.get("browser_download_url")
                break
    if not asset_url:
        raise PortalScraperError(
            "No se encontro un binario win64 de geckodriver en la ultima release."
        )

    archive_path = driver_dir / "geckodriver.zip"
    try:
        download_with_progress(session, asset_url, archive_path)
        with zipfile.ZipFile(archive_path) as zf:
            member = next(
                (name for name in zf.namelist() if name.lower().endswith("geckodriver.exe")),
                None,
            )
            if not member:
                raise PortalScraperError(
                    "La descarga de geckodriver no contiene geckodriver.exe."
                )
            with zf.open(member) as source, driver_path.open("wb") as target:
                shutil.copyfileobj(source, target)
    except Exception as exc:
        raise PortalScraperError(
            "No se pudo descargar o extraer geckodriver automaticamente."
        ) from exc
    finally:
        archive_path.unlink(missing_ok=True)

    return driver_path


def sniff_csv_dialect(sample: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;")
    except csv.Error:
        class DefaultDialect(csv.Dialect):
            delimiter = ";"
            quotechar = '"'
            doublequote = True
            skipinitialspace = False
            lineterminator = "\r\n"
            quoting = csv.QUOTE_MINIMAL

        return DefaultDialect


def slugify(value: str) -> str:
    normalized = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    normalized = re.sub(r"\s+", "_", normalized.strip())
    normalized = re.sub(r"[^a-z0-9_-]", "", normalized)
    return normalized or "municipalidad"


def sanitize_filename(name: str) -> str:
    cleaned = FILENAME_CLEAN_RE.sub("_", name.strip())
    return cleaned.strip("._") or "archivo"


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def extract_url(raw_value: str, base_url: str) -> Optional[str]:
    if not raw_value:
        return None

    href_match = HREF_RE.search(raw_value)
    if href_match:
        return href_match.group(1)

    url_match = URL_RE.search(raw_value)
    if url_match:
        return url_match.group(0)

    soup = BeautifulSoup(raw_value, "html.parser")
    anchor = soup.find("a", href=True)
    if anchor:
        return urljoin(base_url, anchor["href"])

    return None


def normalize_download_url(url: str) -> str:
    parsed = urlparse(url)
    normalized = url

    if "dropbox.com" in parsed.netloc and "dl=1" not in parsed.query:
        separator = "&" if parsed.query else "?"
        normalized = f"{url}{separator}dl=1"
    elif "sharepoint.com" in parsed.netloc and "download=1" not in parsed.query:
        separator = "&" if parsed.query else "?"
        normalized = f"{url}{separator}download=1"
    elif any(
        token in normalized
        for token in (
            "owncloud/",
            "archivostransparencia.mpudahuel.cl/",
            "arch-transparencia.sanantonio.cl/",
        )
    ) and not normalized.rstrip("/").endswith("/download"):
        normalized = normalized.rstrip("/") + "/download"

    return normalized


def parse_date(text: str) -> Optional[datetime]:
    if not text:
        return None

    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            return datetime(
                year=int(match.group("year")),
                month=int(match.group("month")),
                day=int(match.group("day")),
            )
        except ValueError:
            continue

    return None


def extension_from_response(response: requests.Response, url: str) -> str:
    path_suffix = Path(urlparse(url).path).suffix.lower()
    if path_suffix in ALLOWED_EXTENSIONS:
        return path_suffix

    content_type = response.headers.get("Content-Type", "").lower()
    mapping = {
        "application/pdf": ".pdf",
        "application/msword": ".doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/zip": ".zip",
        "application/x-rar-compressed": ".rar",
    }
    for mime, extension in mapping.items():
        if mime in content_type:
            return extension

    return ".bin"


def validate_download_file(path: Path, expected_extension: str) -> str:
    if path.stat().st_size == 0:
        raise PortalScraperError("el archivo descargado esta vacio")

    with path.open("rb") as fh:
        header = fh.read(4096)

    stripped = header.lstrip()
    lower = stripped.lower()
    if lower.startswith((b"<!doctype html", b"<html", b"<head", b"<body")) or b"<html" in lower[:1024]:
        raise PortalScraperError("el servidor devolvio HTML en vez de un documento")

    detected_extension = None
    if b"%PDF-" in header[:1024]:
        detected_extension = ".pdf"
    elif header.startswith(b"PK\x03\x04"):
        detected_extension = ".zip"
    elif header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1") or stripped.startswith(b"{\\rtf"):
        detected_extension = ".doc"
    elif header.startswith(b"Rar!\x1a\x07"):
        detected_extension = ".rar"

    expected_signatures = {
        ".pdf": {".pdf"},
        ".doc": {".doc"},
        ".docx": {".zip"},
        ".zip": {".zip"},
        ".rar": {".rar"},
    }
    allowed = expected_signatures.get(expected_extension)
    if allowed and detected_extension not in allowed:
        raise PortalScraperError(
            f"el contenido no corresponde a la extension esperada {expected_extension}"
        )

    if expected_extension == ".bin" and detected_extension:
        return ".docx" if detected_extension == ".zip" else detected_extension
    return expected_extension


def choose_unique_path(target_path: Path) -> Path:
    if not target_path.exists():
        return target_path

    stem = target_path.stem
    suffix = target_path.suffix
    parent = target_path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def create_webdriver(browser: str, session: requests.Session, tools_root: Path):
    try:
        from selenium import webdriver
    except ImportError as exc:
        raise PortalScraperError(
            "No se pudo importar Selenium. Ejecuta: pip install selenium"
        ) from exc

    browser = browser.lower()
    if browser == "chrome":
        from selenium.webdriver.chrome.options import Options as ChromeOptions

        options = ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument(f"user-agent={USER_AGENT}")
        return webdriver.Chrome(options=options)
    elif browser == "firefox":
        from selenium.webdriver.firefox.options import Options as FirefoxOptions
        from selenium.webdriver.firefox.service import Service as FirefoxService

        options = FirefoxOptions()
        options.add_argument("-headless")
        options.set_preference("general.useragent.override", USER_AGENT)
        firefox_binary = find_firefox_binary()
        if firefox_binary:
            options.binary_location = str(firefox_binary)

        try:
            return webdriver.Firefox(options=options)
        except Exception as primary_exc:
            driver_path = ensure_local_geckodriver(session, tools_root)
            service = FirefoxService(executable_path=str(driver_path))
            try:
                return webdriver.Firefox(service=service, options=options)
            except Exception as fallback_exc:
                raise PortalScraperError(
                    "No se pudo iniciar Firefox con Selenium. "
                    "La descarga automatica de geckodriver tambien fallo."
                ) from fallback_exc

    raise PortalScraperError("Browser no soportado. Usa `firefox` o `chrome`.")


def normalize_portal_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    normalized = "".join(
        char for char in normalized
        if unicodedata.category(char) != "Mn"
    )
    return re.sub(r"\s+", " ", normalized).strip().lower()


def portal_label_matches(label: str, option: str) -> bool:
    normalized_label = normalize_portal_label(label)
    normalized_option = normalize_portal_label(option)
    if not normalized_label or not normalized_option:
        return False
    if normalized_label == normalized_option:
        return True
    if normalized_label == f"mes {normalized_option}":
        return True
    if len(normalized_option) >= 8 and normalized_option in normalized_label:
        return True
    if normalized_option.isdigit():
        return bool(re.search(rf"\b{re.escape(normalized_option)}\b", normalized_label))
    return False


def click_portal_link(driver, text_options: tuple[str, ...], timeout: int = 8) -> None:
    from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait

    last_error = None
    for text in text_options:
        for attempt in range(3):
            try:
                element = WebDriverWait(driver, timeout if attempt == 0 else 2).until(
                    EC.element_to_be_clickable((By.LINK_TEXT, text))
                )
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                element.click()
                return
            except TimeoutException as exc:
                last_error = exc
                break
            except StaleElementReferenceException as exc:
                last_error = exc

    def matching_element(current):
        for element in current.find_elements(By.CSS_SELECTOR, "a.tab-link"):
            try:
                label = element.text
                if any(portal_label_matches(label, option) for option in text_options):
                    return element
            except StaleElementReferenceException:
                continue
        return False

    try:
        element = WebDriverWait(driver, timeout).until(matching_element)
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        element.click()
        return
    except (TimeoutException, StaleElementReferenceException) as exc:
        last_error = exc

    raise PortalScraperError(
        f"No se encontro el enlace del portal: {' / '.join(text_options)}"
    ) from last_error


def click_acta_section_link(driver, timeout: int = 12) -> None:
    from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    preferred_options = (
        "Actas de Concejo Municipal",
        "Actas Concejo Municipal",
        "Actas de Consejo Municipal",
    )
    try:
        click_portal_link(driver, preferred_options, timeout=4)
        return
    except PortalScraperError:
        pass

    def matching_element(current):
        candidates = []
        for element in current.find_elements(By.CSS_SELECTOR, "a.tab-link"):
            try:
                label = normalize_portal_label(element.text)
            except StaleElementReferenceException:
                continue
            if "acta" not in label:
                continue
            if "concejo" not in label and "consejo" not in label:
                continue
            if "video" in label:
                continue
            score = 0
            if "municipal" in label:
                score += 4
            if "organo" in label and "colegiado" in label:
                score += 3
            if "actas de concejo" in label or "actas del concejo" in label:
                score += 2
            candidates.append((score, element))
        return max(candidates, key=lambda item: item[0])[1] if candidates else False

    try:
        element = WebDriverWait(driver, timeout).until(matching_element)
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        element.click()
    except (TimeoutException, StaleElementReferenceException) as exc:
        raise PortalScraperError(
            "No se encontro una seccion de actas del concejo municipal."
        ) from exc


def click_concejo_parent_link(driver, timeout: int = 8) -> None:
    from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    def matching_element(current):
        candidates = []
        for element in current.find_elements(By.CSS_SELECTOR, "a.tab-link"):
            try:
                label = normalize_portal_label(element.text)
            except StaleElementReferenceException:
                continue
            if "concejo" not in label and "consejo" not in label:
                continue
            if any(token in label for token in ("acta", "acuerdo", "video", "instalacion")):
                continue
            score = 2 if "municipal" in label else 1
            candidates.append((score, element))
        return max(candidates, key=lambda item: item[0])[1] if candidates else False

    try:
        element = WebDriverWait(driver, timeout).until(matching_element)
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
        element.click()
    except (TimeoutException, StaleElementReferenceException) as exc:
        raise PortalScraperError(
            "No se encontro un nivel de Concejos Municipales."
        ) from exc


def portal_acta_branches(driver) -> list[str]:
    branches: list[str] = []
    for text in portal_link_texts(driver):
        normalized = normalize_portal_label(text)
        if parse_date(text):
            continue
        if "acta" not in normalized and "sesion" not in normalized:
            continue
        if "extraordinaria" in normalized:
            branches.append(text)
        elif "ordinaria" in normalized:
            branches.append(text)
    return list(dict.fromkeys(branches))


def portal_link_texts(driver) -> list[str]:
    return driver.execute_script(
        """
        return Array.from(document.querySelectorAll('a.tab-link'))
            .map(a => (a.textContent || '').trim())
            .filter(Boolean);
        """
    )


def portal_direct_rows(driver) -> list[dict]:
    return driver.execute_script(
        """
        return Array.from(document.querySelectorAll('tr')).map(row => {
            const link = Array.from(row.querySelectorAll('a[href]')).find(a => {
                const href = a.href || '';
                return href.startsWith('http') && a.getAttribute('href') !== '#';
            });
            if (!link) return null;
            return {
                row_text: (row.innerText || '').replace(/\\s+/g, ' ').trim(),
                row_html: '',
                url: link.href
            };
        }).filter(Boolean);
        """
    )


def sync_session_from_driver(driver, session: requests.Session) -> None:
    user_agent = driver.execute_script("return navigator.userAgent")
    if user_agent:
        session.headers.update({"User-Agent": user_agent})

    for cookie in driver.get_cookies():
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )


def navigate_to_acta_level(
    driver,
    portal_url: str,
    year: int,
    month: Optional[str] = None,
    branch: Optional[str] = None,
) -> None:
    driver.get(portal_url)
    click_portal_link(driver, ("Actos y resoluciones con efectos sobre terceros",))
    try:
        click_acta_section_link(driver)
    except PortalScraperError:
        click_concejo_parent_link(driver)
        click_acta_section_link(driver)
    click_portal_link(driver, (str(year), f"Año {year}", f"Anio {year}"))
    if branch:
        click_portal_link(driver, (branch,))
    if month:
        click_portal_link(driver, (month, f"Mes {month}"))


def discover_acta_rows_with_selenium(
    target: MunicipalityTarget,
    years: set[int],
    browser: str,
    session: requests.Session,
    tools_root: Path,
    max_actas: Optional[int] = None,
) -> tuple[list[dict], list[str]]:
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.support.ui import WebDriverWait

    portal_url = f"{PORTAL_BASE_URL}?org={target.codigo_portal}"
    driver = create_webdriver(browser, session, tools_root)
    driver.set_page_load_timeout(60)
    rows: list[dict] = []
    errors: list[str] = []
    seen_urls: set[str] = set()

    try:
        for year in sorted(years):
            try:
                navigate_to_acta_level(driver, portal_url, year)
            except Exception as exc:
                message = f"{year}: no se pudo abrir: {exc}"
                errors.append(message)
                print(f"[WARN] {target.municipalidad}: {message}", flush=True)
                continue

            try:
                WebDriverWait(driver, 20).until(
                    lambda current: (
                        bool(portal_acta_branches(current))
                        or any(
                            any(portal_label_matches(text, month) for month in MONTHS)
                            for text in portal_link_texts(current)
                        )
                        or any(
                            parse_date(row["row_text"])
                            and parse_date(row["row_text"]).year == year
                            for row in portal_direct_rows(current)
                        )
                    )
                )
            except TimeoutException:
                message = f"{year}: no se encontraron ramas, meses ni actas publicadas"
                errors.append(message)
                print(f"[WARN] {target.municipalidad}: {message}", flush=True)
                continue

            acta_branches = portal_acta_branches(driver)
            if acta_branches:
                print(
                    f"[INFO] {target.municipalidad} {year}: "
                    f"{len(acta_branches)} ramas de sesiones.",
                    flush=True,
                )
                for branch in acta_branches:
                    try:
                        navigate_to_acta_level(
                            driver,
                            portal_url,
                            year,
                            branch=branch,
                        )
                        WebDriverWait(driver, 20).until(
                            lambda current: any(
                                parse_date(row["row_text"])
                                and parse_date(row["row_text"]).year == year
                                for row in portal_direct_rows(current)
                            )
                        )
                        branch_rows = [
                            row
                            for row in portal_direct_rows(driver)
                            if parse_date(row["row_text"])
                            and parse_date(row["row_text"]).year == year
                        ]
                        print(
                            f"[INFO] {target.municipalidad} {year} {branch}: "
                            f"{len(branch_rows)} actas.",
                            flush=True,
                        )
                    except Exception as exc:
                        message = f"{year} {branch}: no se pudo listar: {exc}"
                        errors.append(message)
                        print(
                            f"[WARN] {target.municipalidad}: {message}",
                            flush=True,
                        )
                        continue

                    for direct_row in branch_rows:
                        raw_url = direct_row["url"]
                        if raw_url in seen_urls:
                            continue
                        seen_urls.add(raw_url)
                        rows.append(direct_row)
                        print(
                            f"[LINK] {target.municipalidad}: "
                            f"{direct_row['row_text'][:120]}",
                            flush=True,
                        )
                        if max_actas and len(rows) >= max_actas:
                            return rows, errors
                continue

            available_months = {
                month
                for month in MONTHS
                if any(
                    portal_label_matches(text, month)
                    for text in portal_link_texts(driver)
                )
            }
            year_direct_rows = [
                row
                for row in portal_direct_rows(driver)
                if parse_date(row["row_text"])
                and parse_date(row["row_text"]).year == year
            ]

            if year_direct_rows:
                print(
                    f"[INFO] {target.municipalidad} {year}: "
                    f"{len(year_direct_rows)} actas publicadas directamente.",
                    flush=True,
                )
                for direct_row in year_direct_rows:
                    raw_url = direct_row["url"]
                    if raw_url in seen_urls:
                        continue
                    seen_urls.add(raw_url)
                    rows.append(direct_row)
                    print(
                        f"[LINK] {target.municipalidad}: {direct_row['row_text'][:120]}",
                        flush=True,
                    )
                    if max_actas and len(rows) >= max_actas:
                        return rows, errors
                continue

            print(
                f"[INFO] {target.municipalidad} {year}: "
                f"{len(available_months)} meses publicados.",
                flush=True,
            )

            for month in MONTHS:
                if month not in available_months:
                    continue

                try:
                    navigate_to_acta_level(driver, portal_url, year, month)
                    WebDriverWait(driver, 20).until(
                        lambda current: (
                            any(parse_date(text) for text in portal_link_texts(current))
                            or any(
                                parse_date(row["row_text"])
                                for row in portal_direct_rows(current)
                            )
                        )
                    )
                    record_texts = [
                        text
                        for text in portal_link_texts(driver)
                        if parse_date(text) and parse_date(text).year == year
                    ]
                    record_texts = list(dict.fromkeys(record_texts))
                    direct_rows = [
                        row
                        for row in portal_direct_rows(driver)
                        if parse_date(row["row_text"])
                        and parse_date(row["row_text"]).year == year
                    ]
                    print(
                        f"[INFO] {target.municipalidad} {year} {month}: "
                        f"{len(record_texts) + len(direct_rows)} actas.",
                        flush=True,
                    )
                except Exception as exc:
                    message = f"{year} {month}: no se pudo listar: {exc}"
                    errors.append(message)
                    print(
                        f"[WARN] {target.municipalidad}: {message}",
                        flush=True,
                    )
                    continue

                for direct_row in direct_rows:
                    raw_url = direct_row["url"]
                    if raw_url in seen_urls:
                        continue
                    seen_urls.add(raw_url)
                    rows.append(direct_row)
                    print(
                        f"[LINK] {target.municipalidad}: {direct_row['row_text'][:120]}",
                        flush=True,
                    )
                    if max_actas and len(rows) >= max_actas:
                        return rows, errors

                for record_text in record_texts:
                    try:
                        navigate_to_acta_level(driver, portal_url, year, month)
                        old_url = driver.current_url
                        old_handles = set(driver.window_handles)
                        click_portal_link(driver, (record_text,))

                        WebDriverWait(driver, 20).until(
                            lambda current: (
                                current.current_url != old_url
                                or set(current.window_handles) != old_handles
                            )
                        )

                        new_handles = set(driver.window_handles) - old_handles
                        if new_handles:
                            driver.switch_to.window(new_handles.pop())
                            raw_url = driver.current_url
                            driver.close()
                            driver.switch_to.window(next(iter(old_handles)))
                        else:
                            raw_url = driver.current_url

                        if not raw_url.startswith("http") or raw_url == portal_url:
                            raise PortalScraperError("el enlace no condujo a un documento")
                        if raw_url in seen_urls:
                            continue

                        seen_urls.add(raw_url)
                        rows.append(
                            {
                                "row_text": f"{year} {month} {record_text}",
                                "row_html": "",
                                "url": raw_url,
                            }
                        )
                        print(
                            f"[LINK] {target.municipalidad}: {record_text}",
                            flush=True,
                        )
                        if max_actas and len(rows) >= max_actas:
                            return rows, errors
                    except TimeoutException:
                        message = f"el acta '{record_text}' no abrio un documento"
                        errors.append(message)
                        print(
                            f"[WARN] {target.municipalidad}: {message}.",
                            flush=True,
                        )
                    except Exception as exc:
                        message = f"no se pudo obtener '{record_text}': {exc}"
                        errors.append(message)
                        print(
                            f"[WARN] {target.municipalidad}: {message}",
                            flush=True,
                        )
    finally:
        try:
            driver.get(portal_url)
            sync_session_from_driver(driver, session)
        except Exception as exc:
            print(
                f"[WARN] {target.municipalidad}: no se pudieron sincronizar "
                f"cookies del portal: {exc}",
                flush=True,
            )
        driver.quit()

    return rows, errors


def collect_candidate_rows(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict] = []
    seen_pairs: set[tuple[str, str]] = set()

    table_rows = soup.select("tr")
    for row in table_rows:
        row_text = " ".join(cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"]))
        row_html = str(row)
        if "acta" not in row_text.lower() and "acta" not in row_html.lower():
            continue

        url = None
        for anchor in row.find_all("a", href=True):
            href = anchor["href"]
            if href and href != "#":
                url = urljoin(base_url, href)
                break
        if not url:
            url = extract_url(row_html, base_url)

        pair = (row_text, url or "")
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            candidates.append(
                {
                    "row_text": row_text,
                    "row_html": row_html,
                    "url": url,
                }
            )

    if candidates:
        return candidates

    for anchor in soup.find_all("a", href=True):
        anchor_text = anchor.get_text(" ", strip=True)
        href = urljoin(base_url, anchor["href"])
        parent_text = anchor.parent.get_text(" ", strip=True) if anchor.parent else anchor_text
        combined_text = f"{anchor_text} {parent_text}"
        lower = combined_text.lower()
        if "acta" not in lower:
            continue

        pair = (combined_text, href)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        candidates.append(
            {
                "row_text": combined_text,
                "row_html": str(anchor.parent or anchor),
                "url": href,
            }
        )

    return candidates


def filter_acta_rows(rows: Iterable[dict], years: set[int]) -> list[dict]:
    filtered: list[dict] = []
    for row in rows:
        row_text = row["row_text"]
        row_html = row["row_html"]
        combined = f"{row_text} {row_html}"
        lower = combined.lower()

        if "acta" not in lower:
            continue
        if "concejo" not in lower and "consejo" not in lower:
            continue

        date_value = parse_date(combined)
        if date_value and date_value.year not in years:
            continue

        if not date_value:
            year_match = re.search(r"\b(20\d{2})\b", combined)
            if not year_match or int(year_match.group(1)) not in years:
                continue

        if not row.get("url"):
            continue

        filtered.append(row)

    return filtered


def infer_filename(
    date_value: Optional[datetime],
    description: str,
    extension: str,
) -> str:
    if date_value:
        return f"{date_value.strftime('%Y-%m-%d')}{extension}"

    base_name = sanitize_filename(description)[:80]
    return f"{base_name}{extension}"


def download_file(session: requests.Session, url: str, destination: Path) -> None:
    with session.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with destination.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 64):
                if chunk:
                    fh.write(chunk)


def scrape_municipality(
    target: MunicipalityTarget,
    output_root: Path,
    years: set[int],
    use_selenium: bool,
    browser: str,
    session: requests.Session,
    existing_urls: set[str],
    max_actas: Optional[int] = None,
) -> tuple[list[ActaRecord], list[str]]:
    portal_url = f"{PORTAL_BASE_URL}?org={target.codigo_portal}"
    print(f"[INFO] Revisando {target.municipalidad} ({target.codigo_portal})", flush=True)

    if use_selenium:
        acta_rows, extraction_errors = discover_acta_rows_with_selenium(
            target,
            years,
            browser,
            session,
            output_root,
            max_actas=max_actas,
        )
    else:
        extraction_errors = []
        response = session.get(portal_url, timeout=60)
        response.raise_for_status()
        html = response.text
        candidate_rows = collect_candidate_rows(html, portal_url)
        acta_rows = filter_acta_rows(candidate_rows, years)

    if not acta_rows:
        return [], extraction_errors

    municipality_dir = output_root / slugify(target.municipalidad)
    records: list[ActaRecord] = []
    download_errors: list[str] = []

    for row in acta_rows:
        raw_url = row.get("url") or ""
        temporary_path: Optional[Path] = None
        try:
            raw_url = normalize_download_url(raw_url)
            if raw_url in existing_urls:
                print(f"[SKIP] Ya descargada: {raw_url}", flush=True)
                continue

            date_value = parse_date(f"{row['row_text']} {raw_url}")
            if date_value is None:
                raise PortalScraperError("no se pudo determinar una fecha valida para el acta")
            if date_value.year not in years:
                raise PortalScraperError(
                    f"la fecha detectada {date_value:%Y-%m-%d} no corresponde a los anos solicitados"
                )

            year_dir = municipality_dir / str(date_value.year)
            year_dir.mkdir(parents=True, exist_ok=True)

            with session.get(raw_url, stream=True, timeout=120) as response:
                response.raise_for_status()
                expected_extension = extension_from_response(response, raw_url)
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=".descarga_",
                    suffix=".part",
                    dir=year_dir,
                    delete=False,
                ) as fh:
                    temporary_path = Path(fh.name)
                    for chunk in response.iter_content(chunk_size=1024 * 64):
                        if chunk:
                            fh.write(chunk)

            extension = validate_download_file(temporary_path, expected_extension)
            file_name = infer_filename(date_value, row["row_text"], extension)
            destination = choose_unique_path(year_dir / file_name)
            temporary_path.replace(destination)
            temporary_path = None

            records.append(
                ActaRecord(
                    municipalidad=target.municipalidad,
                    codigo_portal=target.codigo_portal,
                    anio=date_value.year,
                    fecha_acta=date_value.strftime("%Y-%m-%d"),
                    descripcion=row["row_text"][:500],
                    url_descarga=raw_url,
                    ruta_archivo=str(destination),
                    fuente_portal=portal_url,
                )
            )
            append_metadata_record(records[-1], output_root)
            existing_urls.add(raw_url)
            print(f"[OK] {destination.name}", flush=True)
        except Exception as exc:
            message = f"no se pudo descargar {raw_url or '[URL vacia]'}: {exc}"
            download_errors.append(message)
            print(f"[WARN] {target.municipalidad}: {message}", flush=True)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    return records, extraction_errors + download_errors


def load_targets_from_csv(csv_path: Path) -> list[MunicipalityTarget]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        dialect = sniff_csv_dialect(sample)
        reader = csv.DictReader(fh, dialect=dialect)
        required = {"municipalidad", "codigo_portal"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(
                f"El CSV {csv_path} debe contener las columnas: municipalidad,codigo_portal. "
                f"Faltan: {', '.join(sorted(missing))}"
            )

        return [
            MunicipalityTarget(
                municipalidad=row["municipalidad"].strip(),
                codigo_portal=row["codigo_portal"].strip(),
            )
            for row in reader
            if row.get("municipalidad") and row.get("codigo_portal")
        ]


def write_metadata(records: Iterable[ActaRecord], output_root: Path) -> Path:
    metadata_path = output_root / "actas_descargadas_metadata.csv"
    fieldnames = list(ActaRecord.__annotations__.keys())
    merged: dict[str, dict] = {}

    if metadata_path.exists():
        with metadata_path.open("r", encoding="utf-8-sig", newline="") as fh:
            sample = fh.read(4096)
            fh.seek(0)
            reader = csv.DictReader(fh, dialect=sniff_csv_dialect(sample))
            for row in reader:
                if row.get("url_descarga"):
                    merged[row["url_descarga"]] = row

    for record in records:
        merged[record.url_descarga] = asdict(record)

    with metadata_path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(merged.values())

    return metadata_path


def append_metadata_record(record: ActaRecord, output_root: Path) -> None:
    metadata_path = output_root / "actas_descargadas_metadata.csv"
    exists = metadata_path.exists()
    with metadata_path.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=list(ActaRecord.__annotations__.keys()),
            delimiter=";",
        )
        if not exists:
            writer.writeheader()
        writer.writerow(asdict(record))


def load_existing_urls(output_root: Path) -> set[str]:
    metadata_path = output_root / "actas_descargadas_metadata.csv"
    if not metadata_path.exists():
        return set()

    with metadata_path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        reader = csv.DictReader(fh, dialect=sniff_csv_dialect(sample))
        return {
            row["url_descarga"]
            for row in reader
            if row.get("url_descarga")
        }


def append_status(
    output_root: Path,
    target: MunicipalityTarget,
    status: str,
    downloaded: int,
    detail: str = "",
) -> None:
    status_path = output_root / "estado_municipalidades.csv"
    exists = status_path.exists()
    with status_path.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=(
                "fecha_hora",
                "municipalidad",
                "codigo_portal",
                "estado",
                "descargadas",
                "detalle",
            ),
            delimiter=";",
        )
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "fecha_hora": datetime.now().isoformat(timespec="seconds"),
                "municipalidad": target.municipalidad,
                "codigo_portal": target.codigo_portal,
                "estado": status,
                "descargadas": downloaded,
                "detalle": detail,
            }
        )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scrapea actas de concejo municipal desde el Portal de Transparencia "
            "y las guarda organizadas por municipalidad/anio."
        )
    )
    parser.add_argument("--input-csv", type=Path, help="CSV con columnas municipalidad,codigo_portal.")
    parser.add_argument("--municipalidad", help="Nombre de la municipalidad para un scrapeo puntual.")
    parser.add_argument("--codigo-portal", help="Codigo del portal, por ejemplo MU140.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_WORK_ROOT,
        help="Directorio de trabajo y salida. Por defecto: D:/trabajo max",
    )
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        default=list(DEFAULT_YEARS),
        help="Anios a descargar. Por defecto: 2024 2025.",
    )
    parser.add_argument(
        "--selenium",
        action="store_true",
        help="Usa Selenium para renderizar el portal antes de extraer enlaces.",
    )
    parser.add_argument(
        "--browser",
        choices=("firefox", "chrome"),
        default="firefox",
        help="Navegador a usar con --selenium. Por defecto: firefox.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Vuelve a revisar municipalidades ya marcadas como completadas.",
    )
    parser.add_argument(
        "--max-actas",
        type=int,
        help="Limite opcional de actas por municipalidad, util para pruebas.",
    )
    return parser.parse_args(argv)


def resolve_targets(args: argparse.Namespace) -> list[MunicipalityTarget]:
    if args.input_csv:
        return load_targets_from_csv(args.input_csv)

    if args.municipalidad and args.codigo_portal:
        return [MunicipalityTarget(args.municipalidad, args.codigo_portal)]

    raise ValueError(
        "Debes indicar --input-csv o bien la pareja --municipalidad/--codigo-portal."
    )


def load_completed_codes(output_root: Path) -> set[str]:
    status_path = output_root / "estado_municipalidades.csv"
    if not status_path.exists():
        return set()

    with status_path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        reader = csv.DictReader(fh, dialect=sniff_csv_dialect(sample))
        return {
            row["codigo_portal"]
            for row in reader
            if row.get("estado") in {"completado", "sin_actas"} and row.get("codigo_portal")
        }


def load_latest_status_rows(output_root: Path) -> dict[str, dict[str, str]]:
    status_path = output_root / "estado_municipalidades.csv"
    if not status_path.exists():
        return {}

    latest: dict[str, dict[str, str]] = {}
    with status_path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        reader = csv.DictReader(fh, dialect=sniff_csv_dialect(sample))
        for row in reader:
            code = row.get("codigo_portal", "").strip()
            if code:
                latest[code] = row
    return latest


def load_latest_statuses(output_root: Path) -> dict[str, str]:
    return {
        code: row.get("estado", "").strip()
        for code, row in load_latest_status_rows(output_root).items()
        if row.get("estado", "").strip()
    }


def write_targets_csv(path: Path, targets: Iterable[MunicipalityTarget]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=("municipalidad", "codigo_portal"),
            delimiter=";",
        )
        writer.writeheader()
        for target in targets:
            writer.writerow(asdict(target))


def write_followup_csvs(output_root: Path) -> None:
    master_csv = output_root / "municipalidades_portal_345.csv"
    if not master_csv.exists():
        return

    targets = load_targets_from_csv(master_csv)
    latest = load_latest_statuses(output_root)
    pending = [target for target in targets if target.codigo_portal not in latest]
    retry = [
        target
        for target in targets
        if latest.get(target.codigo_portal) in {"error", "parcial"}
    ]
    write_targets_csv(output_root / "municipalidades_pendientes_primera_pasada.csv", pending)
    write_targets_csv(output_root / "municipalidades_para_reintentar.csv", retry)


def write_failure_summary(output_root: Path) -> None:
    failures_path = output_root / "fallas_scrapeo.txt"
    failure_rows = [
        row
        for row in load_latest_status_rows(output_root).values()
        if row.get("estado") in {"error", "parcial"}
    ]
    if not failure_rows:
        failures_path.unlink(missing_ok=True)
        return

    lines = [
        f"{row.get('municipalidad', '')} ({row.get('codigo_portal', '')}) "
        f"[{row.get('estado', '')}]: {row.get('detalle', '')}"
        for row in failure_rows
    ]
    failures_path.write_text("\n".join(lines), encoding="utf-8")


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_pid_file(output_root: Path) -> Path:
    pid_path = output_root / "scraper_nacional.pid"
    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            existing_pid = 0
        if process_is_running(existing_pid):
            raise PortalScraperError(
                f"Ya existe un proceso de scrapeo activo con PID {existing_pid}."
            )
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    return pid_path


def release_pid_file(pid_path: Path) -> None:
    try:
        if pid_path.read_text(encoding="ascii").strip() == str(os.getpid()):
            pid_path.unlink(missing_ok=True)
    except OSError:
        pass


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    output_root = args.output_dir
    output_root.mkdir(parents=True, exist_ok=True)
    targets = resolve_targets(args)
    years = set(args.years)
    session = create_session()
    existing_urls = load_existing_urls(output_root)
    completed_codes = load_completed_codes(output_root) if not args.force else set()

    try:
        pid_path = acquire_pid_file(output_root)
    except PortalScraperError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr, flush=True)
        return 2

    failures: list[str] = []
    interrupted = False

    try:
        write_followup_csvs(output_root)
        for target in targets:
            if target.codigo_portal in completed_codes:
                print(f"[SKIP] Municipalidad completada: {target.municipalidad}", flush=True)
                continue

            try:
                records, extraction_errors = scrape_municipality(
                    target=target,
                    output_root=output_root,
                    years=years,
                    use_selenium=args.selenium,
                    browser=args.browser,
                    session=session,
                    existing_urls=existing_urls,
                    max_actas=args.max_actas,
                )
                metadata_path = write_metadata(records, output_root)
                if extraction_errors:
                    status = "parcial"
                    failures.append(
                        f"{target.municipalidad} ({target.codigo_portal}): "
                        + " | ".join(extraction_errors)
                    )
                elif args.max_actas:
                    status = "prueba"
                elif not records:
                    status = "sin_actas"
                else:
                    status = "completado"
                append_status(
                    output_root,
                    target,
                    status,
                    len(records),
                    " | ".join(extraction_errors),
                )
                write_followup_csvs(output_root)
                write_failure_summary(output_root)
                print(f"[INFO] Metadata actualizada en {metadata_path}", flush=True)
            except Exception as exc:
                failures.append(f"{target.municipalidad} ({target.codigo_portal}): {exc}")
                append_status(output_root, target, "error", 0, str(exc))
                write_followup_csvs(output_root)
                write_failure_summary(output_root)
                print(f"[ERROR] {failures[-1]}", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        interrupted = True
        print("\n[WARN] Ejecucion interrumpida por el usuario.", file=sys.stderr, flush=True)
    finally:
        try:
            write_followup_csvs(output_root)
            write_failure_summary(output_root)
        except Exception as exc:
            print(
                f"[WARN] No se pudieron actualizar las listas de seguimiento: {exc}",
                file=sys.stderr,
                flush=True,
            )
        release_pid_file(pid_path)

    if failures:
        print(
            f"[WARN] Hubo fallas. Revisa {output_root / 'fallas_scrapeo.txt'}",
            file=sys.stderr,
            flush=True,
        )

    if interrupted:
        return 130
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
