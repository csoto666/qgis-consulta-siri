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
"""Buscar un predio por numero de finca o de plano.

Idea y primera version: ing. Jonathan Bustillos Arias (octubre 2026).

Usa el mismo servicio de consulta que el visor publico del SNIT. Ojo: ese
servicio no esta publicado como API -es el que el visor llama por dentro-,
asi que puede cambiar sin aviso. Si un dia deja de responder, el resto del
plugin sigue funcionando; solo esta busqueda queda fuera.
"""
from urllib.parse import urlencode

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QVBoxLayout,
)
from qgis.core import QgsCoordinateReferenceSystem

from . import wms

URL_BUSQUEDA_SNIT = "https://www.snitcr.go.cr/Visor/services/siri"
# El buscador declara su CRS en cada coleccion (medido el 2026-10-19:
# urn:ogc:def:crs:EPSG::8908, CRTM05 sobre CR-SIRGAS) y es ese el que se usa;
# este es solo el respaldo si algun dia deja de declararlo.
CRS_BUSQUEDA = "EPSG:8908"
# Menos que wms.INTENTOS: aqui hay un usuario esperando frente a un cuadro de
# dialogo, y cada intento fallido puede tardar hasta el tope de 60 s.
INTENTOS_BUSQUEDA = 3

PROVINCIAS = [
    ("1", "San José"), ("2", "Alajuela"), ("3", "Cartago"), ("4", "Heredia"),
    ("5", "Guanacaste"), ("6", "Puntarenas"), ("7", "Limón"),
]


def buscar_predio(tipo, valor, provincia=None):
    """Devuelve (resultados, error). resultados es una lista de
    (zona, QgsFeature, QgsCoordinateReferenceSystem); [] si el servicio
    respondio que no hay coincidencias."""
    if tipo == "finca":
        params = {"tipoquery": "finca", "finca": valor}
        if provincia:
            params["provincia"] = provincia
    elif tipo == "plano":
        params = {"tipoquery": "plano", "plano": valor}
    else:
        return None, "Tipo de búsqueda no válido."
    url = URL_BUSQUEDA_SNIT + "?" + urlencode(params)

    objeto, error = None, None
    for intento in range(INTENTOS_BUSQUEDA):
        if intento:
            wms.dormir(wms._espera(intento))
        objeto, error = wms.pedir_json(url)
        if objeto is not None:
            break
    if objeto is None:
        return None, "No se pudo consultar el buscador del SNIT: " + (error or "")
    if not isinstance(objeto, dict) or not objeto.get("result"):
        return [], None

    resultados = []
    datos = objeto.get("data") or {}
    for zona in ("zona_1", "zona_2"):
        coleccion = datos.get(zona)
        if not isinstance(coleccion, dict):
            continue
        crs = wms.crs_geojson(coleccion) or QgsCoordinateReferenceSystem(CRS_BUSQUEDA)
        for feat in wms.entidades_geojson(coleccion.get("features") or []):
            resultados.append((zona, feat, crs))
    return resultados, None


def normalizar(tipo, texto):
    """El numero tal como lo espera el buscador, o None si no sirve."""
    if tipo == "finca":
        digitos = "".join(c for c in texto if c.isdigit())
        if not digitos or len(digitos) > 7:
            return None
        return digitos.zfill(7)
    limpio = "".join(c for c in texto if c.isalnum() or c in "-_/")
    return limpio or None


class DialogoBuscarPredio(QDialog):
    """Pide finca (con provincia) o plano y entrega cada predio encontrado a
    `guardar(zona, feature, crs)`, que lo agrega a la capa de resultados."""

    def __init__(self, guardar, parent=None):
        super().__init__(parent)
        self.guardar = guardar
        self.setWindowTitle("SIRI CR — Buscar predio por finca o plano")
        self.resize(440, 220)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Busca el predio con el mismo servicio que usa el visor público del "
            "SNIT y lo agrega, con todos sus campos, a la capa de resultados.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.tipo = QComboBox()
        self.tipo.addItem("Número de finca", "finca")
        self.tipo.addItem("Número de plano", "plano")
        form.addRow("Buscar por:", self.tipo)

        self.provincia = QComboBox()
        for codigo, nombre in PROVINCIAS:
            self.provincia.addItem(f"{codigo} — {nombre}", codigo)
        form.addRow("Provincia:", self.provincia)

        self.valor = QLineEdit()
        form.addRow("Número:", self.valor)
        layout.addLayout(form)

        botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.boton_buscar = QPushButton("Buscar")
        self.boton_buscar.setDefault(True)
        botones.addButton(self.boton_buscar, QDialogButtonBox.ButtonRole.ActionRole)
        botones.rejected.connect(self.reject)
        self.boton_buscar.clicked.connect(self._buscar)
        layout.addWidget(botones)

        self.tipo.currentIndexChanged.connect(self._actualizar_ayuda)
        self._actualizar_ayuda()

    def _actualizar_ayuda(self, *_):
        finca = self.tipo.currentData() == "finca"
        self.provincia.setEnabled(finca)
        self.valor.setPlaceholderText(
            "Hasta 7 dígitos, p.ej. 234565" if finca
            else "Provincia + inscripción + año, p.ej. 618708012015")

    def _buscar(self):
        tipo = self.tipo.currentData()
        valor = normalizar(tipo, self.valor.text().strip())
        if valor is None:
            QMessageBox.warning(
                self, "SIRI CR",
                "La finca debe tener hasta 7 dígitos." if tipo == "finca"
                else "Digite un número de plano válido.")
            return
        provincia = self.provincia.currentData() if tipo == "finca" else None

        self.boton_buscar.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            resultados, error = buscar_predio(tipo, valor, provincia)
        finally:
            QApplication.restoreOverrideCursor()
            self.boton_buscar.setEnabled(True)

        if error:
            QMessageBox.warning(
                self, "SIRI CR",
                error + "\n\nEse servicio del SNIT responde de forma "
                "intermitente: probá otra vez en unos segundos.")
            return
        if not resultados:
            QMessageBox.information(self, "SIRI CR", "El SNIT no devolvió ningún predio con ese número.")
            return

        for zona, feat, crs in resultados:
            self.guardar(zona, feat, crs)
        if len(resultados) > 1:
            QMessageBox.information(
                self, "SIRI CR",
                f"Se encontraron {len(resultados)} coincidencias y se agregaron "
                "todas a la capa de resultados. Revisá los atributos para "
                "quedarte con la que corresponde.")
        else:
            QMessageBox.information(
                self, "SIRI CR",
                "Predio agregado a la capa de resultados y centrado en el mapa.")
        self.accept()
