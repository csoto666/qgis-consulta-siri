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
"""Cuadro para agregar cualquier otro WMS pegando su URL."""
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout,
)
from qgis.core import QgsProject

from . import wms


class DialogoWmsPorUrl(QDialog):
    """Pegar URL -> Conectar -> escoger capas. Es el mismo flujo del
    Administrador de fuentes de datos de QGIS pero en un solo cuadro y sin
    tener que crear una conexion guardada primero."""

    def __init__(self, padre=None):
        super().__init__(padre)
        self.setWindowTitle("Agregar otro WMS")
        self.setMinimumWidth(560)
        self.capas_leidas = []

        self.campo_url = QLineEdit()
        self.campo_url.setPlaceholderText(
            "https://servidor.go.cr/geoserver/nodo/wms")
        self.combo_version = QComboBox()
        self.combo_version.addItems(["1.1.1", "1.3.0"])
        self.boton_conectar = QPushButton("Conectar")
        self.boton_conectar.clicked.connect(self._conectar)

        fila = QHBoxLayout()
        fila.addWidget(self.campo_url, 1)
        fila.addWidget(self.combo_version)
        fila.addWidget(self.boton_conectar)

        self.lista = QListWidget()
        self.lista.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lista.itemSelectionChanged.connect(self._actualizar_boton_ok)

        self.mensaje = QLabel(
            "Pegá la URL del servicio (la que publica el nodo en "
            "snitcr.go.cr → Servicios OGC) y presioná «Conectar».")
        self.mensaje.setWordWrap(True)

        self.botones = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.botones.accepted.connect(self.accept)
        self.botones.rejected.connect(self.reject)
        self.botones.button(QDialogButtonBox.StandardButton.Ok).setText("Agregar capas")
        self.botones.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

        disposicion = QVBoxLayout(self)
        formulario = QFormLayout()
        formulario.addRow("URL del servicio:", fila)
        disposicion.addLayout(formulario)
        disposicion.addWidget(self.mensaje)
        disposicion.addWidget(self.lista, 1)
        disposicion.addWidget(self.botones)

    # --- acciones -------------------------------------------------------
    def _conectar(self):
        url = self.campo_url.text().strip()
        if not url:
            self.mensaje.setText("Falta la URL del servicio.")
            return

        self.lista.clear()
        self.mensaje.setText("Consultando el servicio…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            capas, error = wms.leer_capabilities(url, self.combo_version.currentText())
        finally:
            QApplication.restoreOverrideCursor()

        if error:
            self.capas_leidas = []
            self.mensaje.setText(f"No se pudo leer el servicio: {error}")
            self._actualizar_boton_ok()
            return

        self.capas_leidas = capas
        for capa in capas:
            marca = "" if capa["consultable"] else "   (no consultable)"
            item = QListWidgetItem(f"{capa['titulo']}  [{capa['layer']}]{marca}")
            item.setData(Qt.ItemDataRole.UserRole, capa)
            self.lista.addItem(item)
        self.mensaje.setText(
            f"{len(capas)} capa(s) disponibles. Escogé una o varias "
            "(Ctrl/⌘ o Shift para seleccionar más de una).")
        self._actualizar_boton_ok()

    def _actualizar_boton_ok(self):
        self.botones.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            bool(self.lista.selectedItems()))

    # --- resultado ------------------------------------------------------
    def seleccion(self):
        """[(url, version, capa, crs)] listo para armar el URI de cada capa."""
        url = self.campo_url.text().strip()
        version = self.combo_version.currentText()
        crs_proyecto = QgsProject.instance().crs().authid()
        salida = []
        for item in self.lista.selectedItems():
            capa = item.data(Qt.ItemDataRole.UserRole)
            salida.append((url, version, capa,
                           wms.escoger_crs(capa["srs"], crs_proyecto)))
        return salida
