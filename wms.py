# -*- coding: utf-8 -*-
"""
Todo lo que tiene que ver con hablar WMS: armar el URI que entiende el
proveedor "wms" de QGIS, cargar la capa al proyecto, y leer un
GetCapabilities para poder ofrecerle al usuario la lista de capas de un
servicio que el mismo pegue.

La idea de fondo: que nadie tenga que pasar por «Administrador de fuentes de
datos -> WMS/WMTS -> Nuevo -> pegar URL -> Conectar -> escoger capa». Eso es
lo que hace que la gente de SINAC termine trabajando sin el catastro a la
vista.
"""
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (
    QgsBlockingNetworkRequest, QgsDataSourceUri, QgsProject, QgsRasterLayer,
    QgsSettings,
)

# Marca que el plugin le pone a las capas que el mismo carga, para despues
# saber cuales puede consultar sin adivinar por el nombre ni por el dominio.
PROP_CONSULTABLE = "consulta_siri/consultable"
PROP_SERVICIO = "consulta_siri/servicio"


def uri_wms(url, layer, crs, version="1.1.1", formato="image/png", estilo=""):
    """URI del proveedor «wms» de QGIS. Se arma con QgsDataSourceUri para que
    el escapado de la URL (que lleva ':' y '/') quede como QGIS lo espera."""
    u = QgsDataSourceUri()
    u.setParam("url", url)
    u.setParam("layers", layer)
    u.setParam("styles", estilo)
    u.setParam("format", formato)
    u.setParam("crs", crs)
    u.setParam("version", version)
    u.setParam("dpiMode", "7")
    return bytes(u.encodedUri()).decode()


def cargar_capa_wms(uri, titulo, clave_servicio=None, consultable=True):
    """Agrega la capa al proyecto (o devuelve la que ya estuviera cargada con
    ese mismo origen, para no llenar la tabla de contenidos de duplicados)."""
    for capa in QgsProject.instance().mapLayers().values():
        if isinstance(capa, QgsRasterLayer) and capa.source() == uri:
            return capa, False

    capa = QgsRasterLayer(uri, titulo, "wms")
    if not capa.isValid():
        return None, False

    capa.setCustomProperty(PROP_CONSULTABLE, bool(consultable))
    if clave_servicio:
        capa.setCustomProperty(PROP_SERVICIO, clave_servicio)
    QgsProject.instance().addMapLayer(capa)
    return capa, True


def guardar_conexion_qgis(nombre, url):
    """Deja el servicio guardado como conexion WMS de QGIS, para que ademas
    aparezca en el panel Explorador y en el Administrador de fuentes de datos
    -util cuando el usuario quiera armar sus propias capas a mano."""
    s = QgsSettings()
    base = f"qgis/connections-wms/{nombre}"
    s.setValue(f"{base}/url", url)
    s.setValue(f"{base}/ignoreGetMapURI", False)
    s.setValue(f"{base}/ignoreGetFeatureInfoURI", False)
    s.setValue(f"{base}/smoothPixmapTransform", False)


# --- GetCapabilities ----------------------------------------------------

def url_capabilities(url, version="1.1.1"):
    """Le pega service/request/version a la URL respetando lo que ya traiga
    (varios nodos del SNIT publican la URL con parametros incluidos)."""
    partes = urlsplit(url)
    q = {k.lower(): v for k, v in parse_qsl(partes.query)}
    q.update({"service": "WMS", "request": "GetCapabilities", "version": version})
    return urlunsplit((partes.scheme, partes.netloc, partes.path, urlencode(q), ""))


def _sin_ns(tag):
    return tag.split("}", 1)[-1]


def _recorrer_capas(elemento, srs_heredados, acumulado):
    """Un WMS anida <Layer> dentro de <Layer>; los SRS y el bbox se heredan
    del padre. Solo interesan las hojas que tienen <Name> (las que se pueden
    pedir); las intermedias son agrupadores."""
    srs = set(srs_heredados)
    nombre = titulo = None
    consultable = elemento.get("queryable") == "1"
    hijos = []

    for hijo in elemento:
        t = _sin_ns(hijo.tag)
        if t == "Name" and nombre is None:
            nombre = (hijo.text or "").strip()
        elif t == "Title" and titulo is None:
            titulo = (hijo.text or "").strip()
        elif t in ("SRS", "CRS") and hijo.text:
            srs.update(x.strip() for x in hijo.text.split() if x.strip())
        elif t == "Layer":
            hijos.append(hijo)

    if nombre:
        acumulado.append({
            "layer": nombre,
            "titulo": titulo or nombre,
            "consultable": consultable,
            "srs": sorted(srs),
        })
    for hijo in hijos:
        _recorrer_capas(hijo, srs, acumulado)


def leer_capabilities(url, version="1.1.1", tiempo_espera=30000):
    """Devuelve (lista_de_capas, error). Usa la pila de red de QGIS para que
    respete el proxy y los certificados configurados en el perfil."""
    peticion = QgsBlockingNetworkRequest()
    if hasattr(peticion, "setTimeout"):   # no existe en QGIS 3.x tempranos
        peticion.setTimeout(tiempo_espera)
    codigo = peticion.get(QNetworkRequest(QUrl(url_capabilities(url, version))), True)
    if codigo != QgsBlockingNetworkRequest.NoError:
        return [], peticion.errorMessage() or "No se pudo contactar el servicio."

    try:
        raiz = ET.fromstring(bytes(peticion.reply().content()))
    except ET.ParseError as e:
        return [], f"La respuesta no es un GetCapabilities válido ({e})."

    capas = []
    for elemento in raiz.iter():
        if _sin_ns(elemento.tag) == "Capability":
            for hijo in elemento:
                if _sin_ns(hijo.tag) == "Layer":
                    _recorrer_capas(hijo, set(), capas)
            break
    if not capas:
        return [], ("El servicio respondió pero no declaró ninguna capa. "
                    "Algunos servidores solo listan sus capas con la versión "
                    "1.1.1 del protocolo (probá con esa).")
    return capas, None


def escoger_crs(srs_disponibles, crs_proyecto):
    """Que CRS pedirle al servidor: el del proyecto si el servicio lo ofrece
    -asi no hay reproyeccion de por medio- y si no, algo que casi todo
    servidor publica."""
    if crs_proyecto in srs_disponibles:
        return crs_proyecto
    for preferido in ("EPSG:8908", "EPSG:5367", "EPSG:4326", "EPSG:3857"):
        if preferido in srs_disponibles:
            return preferido
    return srs_disponibles[0] if srs_disponibles else "EPSG:4326"
