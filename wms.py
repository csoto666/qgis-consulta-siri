# -*- coding: utf-8 -*-
#
# SIRI CR - plugin de QGIS para el catastro del SIRI (Costa Rica)
# Copyright (C) 2026 Carlo Soto Castro
#
# Este programa es software libre: usted puede redistribuirlo y/o modificarlo
# bajo los terminos de la Licencia Publica General GNU publicada por la Free
# Software Foundation, ya sea la version 2 de la Licencia o (a su eleccion)
# cualquier version posterior.
#
# Este programa se distribuye con la esperanza de que sea util, pero SIN
# NINGUNA GARANTIA; ni siquiera la garantia implicita de COMERCIABILIDAD o
# APTITUD PARA UN PROPOSITO DETERMINADO. Vea la Licencia Publica General GNU
# para mas detalles.
#
# Usted deberia haber recibido una copia de la Licencia Publica General GNU
# junto con este programa (archivo LICENSE). Si no, vea
# <https://www.gnu.org/licenses/>.
#
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
import json
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from qgis.PyQt.QtCore import QEventLoop, QUrl, QVariant, QXmlStreamReader
from qgis.PyQt.QtWidgets import QApplication
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (
    QgsBlockingNetworkRequest, QgsCoordinateReferenceSystem, QgsDataSourceUri,
    QgsFeature, QgsField, QgsFields, QgsGeometry, QgsJsonUtils, QgsProject,
    QgsProviderRegistry, QgsRasterLayer, QgsSettings,
)

# El servidor del SIRI responde de forma intermitente: una de cada dos
# peticiones (medido) se va en un 302 hacia /Geoservicios/error en vez de
# devolver el GetCapabilities. No es la red del usuario ni el URI -el mismo
# pedido, repetido, funciona. Por eso todo lo que sale a ese servicio
# reintenta antes de darse por vencido; sin esto la capa se crea invalida y
# el plugin parece roto cuando lo que fallo fue un intento suelto.
INTENTOS = 6
ESPERA_ENTRE_INTENTOS = 1.0   # segundos; crece en cada reintento (ver _espera)
ESPERA_MAXIMA = 4.0


def _espera(intento):
    """Espera creciente: no tiene sentido reintentar cinco veces seguidas a
    un servidor que esta ahogado -conviene darle aire."""
    return min(ESPERA_ENTRE_INTENTOS * (1.5 ** intento), ESPERA_MAXIMA)


def dormir(segundos):
    """Esperar sin dejar QGIS congelado. Con el servicio del SIRI en mal dia
    la espera acumulada llega a la media docena de segundos por capa; un
    time.sleep() pelado deja la ventana sin repintar y el sistema la marca
    como «no responde», que es peor que la demora en si.

    Se excluyen los eventos de entrada a proposito: se quiere que la ventana
    se repinte, no que un segundo clic impaciente en el menu dispare otra
    carga encima de la que ya esta corriendo."""
    fin = time.monotonic() + segundos
    while True:
        restante = fin - time.monotonic()
        if restante <= 0:
            return
        time.sleep(min(restante, 0.05))
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)


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


def cargar_capa_wms(uri, titulo, clave_servicio=None, consultable=True,
                    intentos=INTENTOS):
    """Agrega la capa al proyecto (o devuelve la que ya estuviera cargada con
    ese mismo origen, para no llenar la tabla de contenidos de duplicados).
    Reintenta: crear la capa implica pedir el GetCapabilities, y ese pedido
    falla seguido contra el SIRI."""
    for capa in QgsProject.instance().mapLayers().values():
        if isinstance(capa, QgsRasterLayer) and capa.source() == uri:
            return capa, False

    capa = None
    for intento in range(intentos):
        if intento:
            dormir(_espera(intento))
        capa = QgsRasterLayer(uri, titulo, "wms")
        if capa.isValid():
            break
    if capa is None or not capa.isValid():
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


def _sin_doctype(crudo):
    """Quita la declaracion <!DOCTYPE ...> antes de parsear.

    Dos razones, y las dos importan:

    1. Compatibilidad. El GeoServer del SINAC emite un DOCTYPE mal formado
       -con un & crudo, sin escapar, dentro de la URL del DTD-, y un parser
       estricto como el de Qt corta ahi ("Unexpected '&'"). El GetCapabilities
       es perfectamente valido de ahi en adelante; el DTD no se usa para nada.
    2. Seguridad. En ese mismo DOCTYPE el servidor publica una direccion
       interna suya (addaxgeos1:10080). El DOCTYPE es por donde entran los
       ataques de XML: entidades externas que hacen que el parser lea archivos
       locales o salga a la red (XXE), y entidades internas que se expanden
       hasta agotar la memoria. Si se quita, no hay por donde.
    """
    inicio = crudo.find(b"<!DOCTYPE")
    if inicio < 0:
        return crudo
    # Hay que respetar el subconjunto interno -<!DOCTYPE x [ ... ]>-, donde
    # puede haber '>' que no cierran la declaracion.
    profundidad = 0
    for i in range(inicio + len(b"<!DOCTYPE"), len(crudo)):
        caracter = crudo[i:i + 1]
        if caracter == b"[":
            profundidad += 1
        elif caracter == b"]":
            profundidad -= 1
        elif caracter == b">" and profundidad <= 0:
            return crudo[:inicio] + crudo[i + 1:]
    return crudo[:inicio]


def _leer_texto(lector):
    """Texto del elemento en el que esta parado el lector."""
    return lector.readElementText(QXmlStreamReader.ReadElementTextBehaviour.SkipChildElements).strip()


def _parsear_capabilities(crudo):
    """Saca la lista de capas de un GetCapabilities. Devuelve (capas, error).

    Se usa QXmlStreamReader (de Qt, que QGIS ya trae) y no
    xml.etree.ElementTree a proposito: esto es XML que llega por la red desde
    un servidor que no controlamos, y el parser de la biblioteca estandar es
    vulnerable a los ataques clasicos de XML -entidades que se expanden hasta
    agotar la memoria («billion laughs»), o que hacen que el parser vaya a
    leer un archivo local o una URL (XXE). QXmlStreamReader no resuelve
    entidades externas y limita la expansion de las internas. La alternativa
    habitual, defusedxml, seria una dependencia externa que QGIS no incluye
    -habria que pedirle al usuario que la instale para algo que Qt ya
    resuelve.

    Un WMS anida <Layer> dentro de <Layer> y los SRS se heredan del padre, asi
    que se lleva una pila con el contexto de cada capa abierta. Solo interesan
    las que tienen <Name> (las que se pueden pedir); las intermedias son
    agrupadores. Y solo se miran los <Name>/<Title>/<SRS> que cuelgan
    directamente de un <Layer>: dentro de <Style> vuelve a haber <Name>, y el
    <Service> del encabezado tiene el suyo.
    """
    lector = QXmlStreamReader(_sin_doctype(crudo))
    pila_elementos = []      # nombres de elementos abiertos
    pila_capas = []          # contexto de cada <Layer> abierto
    capas = []
    en_capability = False
    orden = 0

    while not lector.atEnd():
        ficha = lector.readNext()

        if ficha == QXmlStreamReader.TokenType.StartElement:
            etiqueta = lector.name()
            etiqueta = etiqueta if isinstance(etiqueta, str) else str(etiqueta)
            padre = pila_elementos[-1] if pila_elementos else None

            # Un ServiceException es justamente la respuesta intermitente del
            # SIRI: quien llama tiene que reintentar, no rendirse.
            if etiqueta == "ServiceExceptionReport":
                return [], ("El servicio devolvió un error "
                            "(respuesta intermitente del servidor).")

            if etiqueta == "Capability":
                en_capability = True

            if en_capability and etiqueta == "Layer":
                heredados = set(pila_capas[-1]["srs"]) if pila_capas else set()
                orden += 1
                pila_capas.append({
                    "layer": None,
                    "titulo": None,
                    "consultable": str(lector.attributes().value("queryable")) == "1",
                    "srs": heredados,
                    "orden": orden,
                })

            elif pila_capas and padre == "Layer":
                actual = pila_capas[-1]
                if etiqueta == "Name" and actual["layer"] is None:
                    actual["layer"] = _leer_texto(lector)
                    continue          # readElementText ya consumio el cierre
                if etiqueta == "Title" and actual["titulo"] is None:
                    actual["titulo"] = _leer_texto(lector)
                    continue
                if etiqueta in ("SRS", "CRS"):
                    actual["srs"].update(
                        x for x in _leer_texto(lector).split() if x)
                    continue

            pila_elementos.append(etiqueta)

        elif ficha == QXmlStreamReader.TokenType.EndElement:
            etiqueta = lector.name()
            etiqueta = etiqueta if isinstance(etiqueta, str) else str(etiqueta)
            if pila_elementos:
                pila_elementos.pop()
            if etiqueta == "Capability":
                en_capability = False
            elif etiqueta == "Layer" and pila_capas:
                capa = pila_capas.pop()
                if capa["layer"]:
                    capas.append({
                        "layer": capa["layer"],
                        "titulo": capa["titulo"] or capa["layer"],
                        "consultable": capa["consultable"],
                        "srs": sorted(capa["srs"]),
                        "orden": capa["orden"],
                    })

    if lector.hasError():
        return [], f"La respuesta no es un GetCapabilities válido ({lector.errorString()})."

    # Se recogen al cerrar cada <Layer>, o sea de adentro hacia afuera; se
    # devuelven en el orden en que aparecen en el documento, que es el que el
    # usuario ve en la lista.
    capas.sort(key=lambda c: c.pop("orden"))
    return capas, None


def _pedir_capabilities(url, version, tiempo_espera):
    """Un intento. Devuelve (xml_crudo, error)."""
    peticion = QgsBlockingNetworkRequest()
    if hasattr(peticion, "setTimeout"):   # no existe en QGIS 3.x tempranos
        peticion.setTimeout(tiempo_espera)
    codigo = peticion.get(QNetworkRequest(QUrl(url_capabilities(url, version))), True)
    if codigo != QgsBlockingNetworkRequest.ErrorCode.NoError:
        return None, peticion.errorMessage() or "No se pudo contactar el servicio."
    return bytes(peticion.reply().content()), None


# 60 s, no 30: medido el 2026-09-10, cuando el SIRI esta degradado las
# respuestas que SI llegan tardan 24-25 s. Con 30 s de tope se cortaban justo
# las peticiones que iban a funcionar, y el plugin reportaba "caido" un
# servicio que solo estaba lento. Es el mismo tope que usa QGIS por omision.
TIEMPO_ESPERA = 60000   # milisegundos


def leer_capabilities(url, version="1.1.1", tiempo_espera=TIEMPO_ESPERA, intentos=INTENTOS):
    """Devuelve (lista_de_capas, error). Usa la pila de red de QGIS para que
    respete el proxy y los certificados configurados en el perfil, y reintenta
    -ver el comentario de INTENTOS."""
    error = None
    for intento in range(intentos):
        if intento:
            dormir(_espera(intento))

        crudo, error = _pedir_capabilities(url, version, tiempo_espera)
        if crudo is None:
            continue

        capas, error = _parsear_capabilities(crudo)
        if capas:
            return capas, None
        if error:
            # Puede ser un ServiceException -la respuesta intermitente del
            # SIRI- o XML cortado a medias: en los dos casos vale reintentar.
            continue

        error = ("El servicio respondió pero no declaró ninguna capa. "
                 "Algunos servidores solo listan sus capas con la versión "
                 "1.1.1 del protocolo (probá con esa).")

    return [], error


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


# --- GeoJSON: GetFeatureInfo sin identify() y buscador del SNIT ------------
#
# En algunos builds de QGIS 4 (Qt6/PyQt6), QgsRasterIdentifyResult.results()
# revienta al convertir a Python la QgsFeatureStoreList que trae adentro
# -reportado por Jonathan Bustillos con QGIS 4.2.3-. En QGIS 3 funciona. Por
# eso el plugin sigue usando identify() donde sirve, y solo cuando esa
# conversion falla pide el GetFeatureInfo directo, en GeoJSON, y arma las
# entidades a mano con lo de abajo.

def pedir_json(url, tiempo_espera=TIEMPO_ESPERA):
    """Un GET con la pila de red de QGIS (respeta proxy y certificados del
    perfil). Devuelve (objeto, error). Un solo intento: reintentar le toca a
    quien llama, que sabe cuando una respuesta vacia es fallo y cuando no."""
    peticion = QgsBlockingNetworkRequest()
    if hasattr(peticion, "setTimeout"):
        peticion.setTimeout(tiempo_espera)
    solicitud = QNetworkRequest(QUrl(url))
    solicitud.setRawHeader(b"User-Agent", b"QGIS-SIRI-CR")
    codigo = peticion.get(solicitud, True)
    if codigo != QgsBlockingNetworkRequest.ErrorCode.NoError:
        return None, peticion.errorMessage() or "No se pudo contactar el servicio."
    crudo = bytes(peticion.reply().content())
    try:
        return json.loads(crudo.decode("utf-8-sig")), None
    except ValueError:
        # El SIRI intermitente contesta con una pagina HTML de error en vez
        # del JSON; se devuelve el comienzo para poder diagnosticarlo.
        return None, "Respuesta que no es JSON: " + crudo[:200].decode("utf-8", "replace")


def _tipo_qvariant(valor):
    """Tipo de campo para un valor GeoJSON; conserva numeros y booleanos."""
    if isinstance(valor, bool):
        return getattr(QVariant, "Bool", QVariant.String)
    if isinstance(valor, int):
        return getattr(QVariant, "LongLong", QVariant.Int)
    if isinstance(valor, float):
        return QVariant.Double
    return QVariant.String


def geometria_geojson(geom_json):
    """QgsGeometry desde el objeto 'geometry' de un GeoJSON. Se usa
    QgsJsonUtils.geometryFromGeoJson donde existe (QGIS 3.36+ y 4) y GDAL/OGR,
    que viene con todo QGIS, en las versiones anteriores."""
    texto = json.dumps(geom_json)
    if hasattr(QgsJsonUtils, "geometryFromGeoJson"):
        return QgsJsonUtils.geometryFromGeoJson(texto)
    from osgeo import ogr
    ogr_geom = ogr.CreateGeometryFromJson(texto)
    geom = QgsGeometry()
    if ogr_geom is not None:
        geom.fromWkb(bytes(ogr_geom.ExportToWkb()))
    return geom


def crs_geojson(objeto):
    """El CRS que declara un FeatureCollection (miembro 'crs', que GeoServer
    sigue emitiendo aunque RFC 7946 lo haya quitado), o None si no declara."""
    try:
        nombre = objeto["crs"]["properties"]["name"]
    except (KeyError, TypeError):
        return None
    crs = QgsCoordinateReferenceSystem.fromOgcWmsCrs(str(nombre))
    return crs if crs.isValid() else None


def entidades_geojson(features_json):
    """QgsFeature por cada elemento con geometria. Todos comparten el mismo
    esquema -la union de las propiedades de todos-, y el tipo de cada campo
    sale del primer valor no nulo que aparezca."""
    nombres = []
    for item in features_json:
        for nombre in (item.get("properties") or {}):
            if nombre not in nombres:
                nombres.append(nombre)
    campos = QgsFields()
    for nombre in nombres:
        muestra = next((item["properties"][nombre] for item in features_json
                        if (item.get("properties") or {}).get(nombre) is not None), None)
        campos.append(QgsField(str(nombre), _tipo_qvariant(muestra)))

    salida = []
    for item in features_json:
        if not item.get("geometry"):
            continue
        geom = geometria_geojson(item["geometry"])
        if geom.isNull() or geom.isEmpty():
            continue
        feat = QgsFeature(campos)
        feat.setGeometry(geom)
        props = item.get("properties") or {}
        feat.setAttributes([props.get(nombre) for nombre in nombres])
        salida.append(feat)
    return salida


def getfeatureinfo_geojson(capa, punto, extent, ancho, alto):
    """Un GetFeatureInfo en application/json contra una capa WMS ya cargada,
    con el punto y la extension en el CRS de la capa. Devuelve
    (entidades, crs_de_la_respuesta, error); entidades es None si el servicio
    no respondio, y [] si respondio que no hay nada en ese punto."""
    uri = QgsProviderRegistry.instance().decodeUri("wms", capa.source())
    url = uri.get("url", "")
    capas = uri.get("layers", "")
    estilos = uri.get("styles", "")
    if isinstance(capas, (list, tuple)):
        capas = ",".join(str(c) for c in capas)
    if isinstance(estilos, (list, tuple)):
        estilos = ",".join(str(e) for e in estilos)
    version = uri.get("version") or "1.1.1"
    crs = capa.crs()

    ancho, alto = max(1, int(ancho)), max(1, int(alto))
    i = int((punto.x() - extent.xMinimum()) / max(extent.width(), 1e-30) * ancho)
    j = int((extent.yMaximum() - punto.y()) / max(extent.height(), 1e-30) * alto)
    i, j = max(0, min(ancho - 1, i)), max(0, min(alto - 1, j))

    caja = [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()]
    # WMS 1.3.0 respeta el orden de ejes del CRS (lat/lon en EPSG:4326); 1.1.1 no.
    if version.startswith("1.3") and crs.hasAxisInverted():
        caja = [caja[1], caja[0], caja[3], caja[2]]

    partes = urlsplit(url)
    q = dict(parse_qsl(partes.query, keep_blank_values=True))
    q.update({
        "SERVICE": "WMS", "VERSION": version, "REQUEST": "GetFeatureInfo",
        "LAYERS": capas, "QUERY_LAYERS": capas, "STYLES": estilos,
        "FORMAT": uri.get("format") or "image/png",
        "INFO_FORMAT": "application/json", "FEATURE_COUNT": "10",
        "BBOX": ",".join(format(v, ".15g") for v in caja),
        "WIDTH": str(ancho), "HEIGHT": str(alto),
    })
    if version.startswith("1.3"):
        q.update({"CRS": crs.authid(), "I": str(i), "J": str(j)})
    else:
        q.update({"SRS": crs.authid(), "X": str(i), "Y": str(j)})
    url_consulta = urlunsplit((partes.scheme, partes.netloc, partes.path, urlencode(q), ""))

    objeto, error = pedir_json(url_consulta)
    if objeto is None:
        return None, None, error
    if not isinstance(objeto, dict) or "features" not in objeto:
        return None, None, "El servicio no devolvió un FeatureCollection."
    entidades = entidades_geojson(objeto["features"])
    crs_respuesta = crs_geojson(objeto)

    # En GeoJSON el GeoServer del SIRI aplica una tolerancia de varios pixeles
    # y devuelve tambien los predios vecinos (medido: 5 donde identify() da 1),
    # sin garantia de orden. Se deja el que contiene el punto, si hay uno.
    if crs_respuesta is None or crs_respuesta == crs:
        punto_geom = QgsGeometry.fromPointXY(punto)
        contienen = [f for f in entidades if f.geometry().contains(punto_geom)]
        if contienen:
            entidades = contienen
    return entidades, crs_respuesta, None
