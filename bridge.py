from PyQt4.QtXml import *
from PyQt4.QtCore import *
from PyQt4.QtGui import *
from qgis.core import *
import os


class LayerExporter():
    """
    Export a layer and its dependencies including styling information
    """
    def __init__(self, basepath):
        self.traversed_layers = list()
        self.basepath = basepath

    def add_dependency(self, elem, doc, lyr):
        depno = doc.createElement('dependency')
        try:
            lyr.id()
        except AttributeError:
            lyr = QgsMapLayerRegistry.instance().mapLayer(lyr)

        lname = self.save_layer_definition(lyr)

        depnotext = doc.createTextNode(lname)
        depno.appendChild(depnotext)
        elem.appendChild(depno)
        return lname

    def write_layer_tree_path(self, lt_node, element):
        doc = element.ownerDocument()
        el = doc.createElement('layer-tree-group')
        el.setAttribute('name', lt_node.name())
        if QgsProject.instance().layerTreeRoot() != lt_node.parent():
            parent_element = self.write_layer_tree_path(lt_node.parent(), element)
            parent_element.appendChild(el)
        else:
            element.appendChild(el)
        return el

    def save_layer_definition(self, l):
        layer_name = QgsDataSourceURI(l.dataProvider().dataSourceUri()).table()

        if layer_name in self.traversed_layers:
            return layer_name
        self.traversed_layers.append(layer_name)

        basename = os.path.join(self.basepath, layer_name)

        doc = QDomDocument()
        root_node = doc.createElement('maplayer')
        doc.appendChild(root_node)

        dependency_node = doc.createElement('dependencies')

        l.writeLayerXML(root_node, doc)
        layer_id_node = doc.createTextNode(layer_name)
        id_node = doc.createElement('id')
        id_node.appendChild(layer_id_node)

        root_node.replaceChild(id_node, root_node.firstChildElement('id'))

        # Save relations
        rm = QgsProject.instance().relationManager()

        relations_node = doc.createElement('relations')

        for r in rm.referencingRelations(l):
            lyr = r.referencedLayer()
            lid = self.add_dependency(dependency_node, doc, lyr)
            r.setReferencingLayer(layer_name)
            r.setReferencedLayer(lid)
            r.writeXML(relations_node, doc)

        for r in rm.referencedRelations(l):
            lyr = r.referencingLayer()
            lid = self.add_dependency(dependency_node, doc, lyr)
            r.setReferencedLayer(layer_name)
            r.setReferencingLayer(lid)
            r.writeXML(relations_node, doc)

        root_node.appendChild(relations_node)

        # Save layer tree position
        lt_root = QgsProject.instance().layerTreeRoot()
        lt_layer = lt_root.findLayer(l.id())
        if lt_layer.parent() != lt_root:
            self.write_layer_tree_path(lt_layer.parent(), root_node)

        (error, res) = l.saveNamedStyle(basename + '.qml')

        qmldoc = QDomDocument('qml')
        qmlfile = QFile(basename + '.qml')
        qmlfile.open(QIODevice.ReadOnly)
        qmldoc.setContent(qmlfile)
        qmlfile.close()
        qmlroot_elem = qmldoc.documentElement()

        for i in range(l.pendingFields().count()):
            if l.editorWidgetV2(i) == 'ValueRelation':
                cfg = l.editorWidgetV2Config(i)
                lid = self.add_dependency(dependency_node, doc, cfg['Layer'])
                edit_node = qmlroot_elem.firstChildElement('edittypes')
                edit_type_nodes = edit_node.elementsByTagName('edittype')
                for j in range(edit_type_nodes.count()):
                    et = edit_type_nodes.at(j)
                    if et.toElement().attribute('name') == l.pendingFields()[i].name():
                        edit_cfg_node = et.toElement().firstChildElement('widgetv2config')
                        edit_cfg_node.setAttribute('Layer', lid)

        root_node.appendChild(dependency_node)

        # Clean qlf document from styling information
        for qmlnode in ['edittypes',
                        'editform',
                        'editforminit',
                        'featformsuppress',
                        'annotationform',
                        'editorlayout',
                        'excludeAttributesWMS',
                        'excludeAttributesWFS',
                        'attributeactions']:
            root_node.removeChild(root_node.firstChildElement(qmlnode))

        with open(basename + '.qml', 'w') as f:
            f.write(qmldoc.toString().encode('utf-8'))

        with open(basename + '.qlf', 'w') as f:
            f.write(doc.toString().encode('utf-8'))

        return layer_name


class LayerImporter():
    """
    Imports a layer including its dependencies and styling information from a .qlf file in a layersaver folder
    """

    def __init__(self, basepath):
        self.relations=list()
        self.basepath = basepath
        self.loaded_layers = list()

    def read_layer_tree_path(self, element, lt_node):
        el = element.firstChildElement('layer-tree-group')
        lt_subnode = lt_node

        if not element.isNull():
            found = False

            for lt_candidate in lt_node.children():
                if isinstance(lt_candidate, QgsLayerTreeGroup) and lt_candidate.name() == element.attribute('name'):
                    lt_subnode = self.read_layer_tree_path(el, lt_candidate)
                    found = True
                    break

            if not found:
                lt_newnode = lt_node.insertGroup(0, element.attribute('name'))
                lt_newnode.setExpanded(False)
                lt_subnode = self.read_layer_tree_path(el, lt_newnode)

        return lt_subnode

    def load_layer_definition(self, layer):
        if QgsMapLayerRegistry.instance().mapLayer(layer) or layer in self.loaded_layers:
            return

        basename = os.path.join(self.basepath, layer)

        self.loaded_layers.append(layer)
        doc = QDomDocument('qlf')
        qlffile = QFile(basename + '.qlf')
        qlffile.open(QIODevice.ReadOnly)
        doc.setContent(qlffile)
        qlffile.close()
        maplayer_node = doc.documentElement()

        deps_node = maplayer_node.firstChildElement('dependencies')
        deps_nodes = deps_node.elementsByTagName('dependency')
        for i in range(deps_nodes.count()):
            self.load_layer_definition(deps_nodes.at(i).toElement().text())

        if maplayer_node.attribute('type') == 'vector':
            layer = QgsVectorLayer()
        elif maplayer_node.attribute('type') == 'raster':
            layer = QgsRasterLayer()
        else:
            raise TypeError('Cannot handle layer ' + layer + ' of type ' + maplayer_node.attribute(
                'type') + ' - ' + maplayer_node.tagName())

        layer.readLayerXML(doc.documentElement())
        QgsMapLayerRegistry.instance().addMapLayer(layer, False)

        lt_node = self.read_layer_tree_path(maplayer_node.firstChildElement('layer-tree-group'),
                                            QgsProject.instance().layerTreeRoot())
        lt_node.insertLayer(-1, layer)

        relnode = maplayer_node.firstChildElement('relations')
        relnodes = relnode.elementsByTagName('relation')
        QMessageBox.information( None, 'Relation', layer.id() + ' - ' + str(relnodes.count()))
        for i in range(relnodes.count()):
            self.relations.append(relnodes.at(i))

    def load_layer(self,layer):
        assert len(self.relations) == 0
        self.load_layer_definition(layer)
        QMessageBox.information( None, 'Relation', 'RELA ' + str(len(self.relations) ))
        for r in self.relations:
            rel = QgsRelation.createFromXML(r)
            QMessageBox.information( None, 'Relation', 'Adding relation' + rel.id() )
            QgsProject.instance().relationManager().addRelation(rel)

        self.relations = list()
