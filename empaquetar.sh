#!/bin/sh
# Arma el ZIP instalable en QGIS (Complementos > Instalar desde ZIP).
#
# Por que no sirve el "Download ZIP" de GitHub: ese genera una carpeta raiz
# llamada "qgis-consulta-siri-main", y QGIS usa el nombre de esa carpeta como
# nombre del complemento. Quien lo instale asi termina con el plugin
# registrado bajo un nombre distinto -y si despues lo instala bien, QGIS
# carga las dos copias a la vez, con dos barras de herramientas iguales.
# Este script deja la carpeta raiz con el nombre correcto: consulta_siri.
set -e

PAQUETE=consulta_siri
VERSION=$(sed -n 's/^version=//p' metadata.txt)
SALIDA="$PAQUETE-$VERSION.zip"
TEMP=$(mktemp -d)

mkdir "$TEMP/$PAQUETE"
# Solo lo que el plugin necesita en tiempo de ejecucion, mas README y LICENSE.
for f in __init__.py plugin.py wms.py dialogos.py servicios.py \
         metadata.txt icon.png icon.svg README.md LICENSE; do
    cp "$f" "$TEMP/$PAQUETE/"
done

rm -f "$SALIDA"
(cd "$TEMP" && zip -q -r - "$PAQUETE") > "$SALIDA"
rm -rf "$TEMP"

echo "Listo: $SALIDA"
unzip -l "$SALIDA" | tail -n +2
