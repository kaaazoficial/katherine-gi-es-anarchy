"""Heuristic link inspection. Signals are advisory, not antivirus verdicts."""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit


_ADULT_DOMAINS = (
    "pornhub.", "xvideos.", "xnxx.", "xhamster.", "redtube.",
    "onlyfans.", "fansly.", "chaturbate.", "stripchat.",
    "rule34.", "nhentai.", "eporner.", "youporn.",
)
_ADULT_MARKERS = (
    "pornografia", "pornographic", "porn videos", "adult videos",
    "conteudo adulto", "contenido adulto", "conteudo +18", "contenido +18",
    "18+", "nsfw", "hentai", "explicit nudity", "nudity",
    "nude photos", "videos adultos", "videos porno",
)
_SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "is.gd",
    "cutt.ly", "shorturl.at", "rebrand.ly", "rb.gy", "lnk.bio",
}
_SUSPICIOUS_TLDS = {
    "zip", "mov", "click", "top", "work", "gq", "tk", "ml",
    "cf", "ga", "icu", "buzz", "rest",
}
_EXECUTABLE_EXTENSIONS = {
    ".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".scr",
    ".apk", ".jar", ".dmg", ".pkg", ".com",
}


def _host(value: str) -> str:
    try:
        return (urlsplit(str(value or "")).hostname or "").rstrip(".").casefold()
    except ValueError:
        return ""


def _looks_adult(host: str, title: str, content: str) -> bool:
    if any(marker in host for marker in _ADULT_DOMAINS):
        return True
    text = f"{title} {content[:5000]}".casefold()
    matches = sum(1 for marker in _ADULT_MARKERS if marker in text)
    return matches >= 1


def inspect_link_signals(
    url: str,
    *,
    final_url: str = "",
    redirect_chain: list[str] | tuple[str, ...] | None = None,
    title: str = "",
    page_text: str = "",
) -> dict:
    """Classify visible URL/page signals without downloading files or running scripts."""
    raw = str(url or "").strip()
    chain = [str(item) for item in (redirect_chain or []) if str(item).strip()]
    if not chain and raw:
        chain = [raw]
    destination = str(final_url or (chain[-1] if chain else raw)).strip()
    host = _host(raw)
    final_host = _host(destination)
    reasons: list[str] = []
    warnings: list[str] = []
    suspicious_points = 0

    try:
        parsed = urlsplit(raw)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            reasons.append("La URL no usa HTTPS o no contiene un dominio válido.")
            suspicious_points += 2
        if parsed.username is not None or parsed.password is not None:
            reasons.append("La dirección incluye credenciales antes del dominio.")
            suspicious_points += 3
    except ValueError:
        return {
            "risk": "alto", "risk_label": "ALTO RIESGO",
            "adult": False, "adult_label": "No determinado",
            "malicious_suspected": True,
            "reasons": ["La URL tiene una estructura inválida."],
            "warnings": ["No abras el enlace."],
            "checked_host": host or "desconocido",
        }

    if host:
        try:
            address = ipaddress.ip_address(host)
            if address.is_global:
                reasons.append("El destino usa una dirección IP directa en lugar de un dominio reconocible.")
                suspicious_points += 2
        except ValueError:
            if host.startswith("xn--") or ".xn--" in host:
                reasons.append("El dominio usa caracteres internacionalizados; comprueba que no suplante otro nombre.")
                suspicious_points += 2
            tld = host.rsplit(".", 1)[-1]
            if tld in _SUSPICIOUS_TLDS:
                warnings.append(f"El dominio termina en .{tld}, una extensión que merece revisión adicional.")
                suspicious_points += 1
            if host in _SHORTENERS:
                warnings.append("Es un acortador: el destino real debe revisarse antes de confiar en el enlace.")
                suspicious_points += 1
        if any(host.endswith(suffix) for suffix in ("duckapps.com.br",)):
            warnings.append("El dominio pertenece a Ducks; eso no garantiza que cada publicación o enlace externo alojado allí sea seguro.")

    if final_host and host and final_host != host:
        warnings.append(f"El enlace terminó en otro dominio: {final_host}.")
        suspicious_points += 1
    if len(chain) > 3:
        warnings.append(f"Se observaron {len(chain) - 1} redirecciones.")
        suspicious_points += 1

    try:
        path = urlsplit(destination).path.casefold()
    except ValueError:
        path = ""
    extension = next(
        (suffix for suffix in _EXECUTABLE_EXTENSIONS if path.endswith(suffix)),
        "",
    )
    if extension:
        reasons.append(f"El destino parece apuntar a un archivo ejecutable ({extension}). No lo descargues desde un enlace no verificado.")
        suspicious_points += 3

    combined_text = f"{title} {page_text[:5000]}".casefold()
    credential_bait = (
        any(term in combined_text for term in ("verify your account", "confirm your account", "verifica tu cuenta", "confirme sua conta", "restablece tu contraseña", "reset your password"))
        and any(term in combined_text for term in ("password", "contraseña", "senha", "login", "iniciar sesión"))
    )
    if credential_bait:
        warnings.append("La página parece solicitar credenciales o la verificación de una cuenta.")
        suspicious_points += 2

    adult = _looks_adult(final_host or host, title, page_text)
    if adult:
        adult_label = "Posible contenido +18"
        warnings.append("Se detectaron indicadores de contenido para adultos. Evita compartirlo en espacios donde no esté permitido.")
    elif page_text or title:
        adult_label = "No se detectaron indicadores +18"
    else:
        adult_label = "No verificable: la página no pudo leerse"

    malicious_suspected = suspicious_points >= 3
    if malicious_suspected:
        risk = "alto"
        risk_label = "ALTO RIESGO · NO ABRIR"
    elif suspicious_points:
        risk = "medio"
        risk_label = "REVISAR ANTES DE ABRIR"
    elif page_text or title:
        risk = "bajo"
        risk_label = "SIN INDICIOS EVIDENTES"
    else:
        risk = "unknown"
        risk_label = "NO VERIFICADO"

    if not malicious_suspected and not suspicious_points:
        warnings.append("No se detectaron señales sospechosas conocidas; esto no certifica que el sitio sea seguro.")
    if not page_text and not title:
        warnings.append("No se pudo inspeccionar el contenido de la página; solo se evaluó la estructura de la URL.")

    return {
        "risk": risk,
        "risk_label": risk_label,
        "adult": adult,
        "adult_label": adult_label,
        "malicious_suspected": malicious_suspected,
        "reasons": reasons,
        "warnings": warnings,
        "checked_host": final_host or host or "desconocido",
        "redirect_count": max(0, len(chain) - 1),
    }


def format_link_signals(result: dict) -> str:
    """Render a short, readable link inspection report."""
    lines = [
        f"Seguridad: {result.get('risk_label', 'NO VERIFICADO')}",
        f"Contenido: {result.get('adult_label', 'No determinado')}",
        f"Dominio final: {result.get('checked_host') or 'desconocido'}",
    ]
    for label, key in (("Indicadores", "reasons"), ("Advertencias", "warnings")):
        values = [str(item) for item in (result.get(key) or []) if str(item).strip()]
        if values:
            lines.append(label + ":")
            lines.extend(f"• {item}" for item in values[:5])
    return "\n".join(lines)
