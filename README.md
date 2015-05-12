lib_layer_saver
================

This library facilitates importing and exporting of QGIS layers. The layers are
split in two different files:

 * `.qlf` files contain layer data information
 * `.qml` files contain layer style information

This library is able to extract dependency information from a project by
inspecting the relations and ValueRelation widgets. If you save and reload a
layer with dependencies, these will be loaded as well.
