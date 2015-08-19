# -*- coding: utf-8 -*-

from PyQt4.QtXml import *
from PyQt4.QtCore import *
from PyQt4.QtGui import *
from qgis.core import *
import os
import re


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
        '''
        Recursive method to write the position of a layer in the layer tree.
        Traverses all ancestor groups and creates appropriate XML elements.
        :param lt_node: The layer tree node to check for a parent
        :param element: The XML element to which to add further elements
        :return: An XML element with a layer tree group
        '''
        doc = element.ownerDocument()
        el = doc.createElement('layer-tree-group')
        el.setAttribute('name', lt_node.name())
        if QgsProject.instance().layerTreeRoot() != lt_node.parent():
            parent_element = self.write_layer_tree_path(lt_node.parent(), element)
            parent_element.appendChild(el)
        else:
            element.appendChild(el)
        return el

    def to_layer_id(self, l):
        # QGIS requires layer ids to be at least 11 chars long
        return QgsDataSourceURI(l.dataProvider().dataSourceUri()).table().ljust(11, '_')

    def save_layer_definition(self, l):
        layer_name = self.to_layer_id(l)

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

        # Clean qlf document from styling information which is present in the qml file
        for qmlnode in ['edittypes',
                        'editform',
                        'attributeEditorForm',
                        'editforminit',
                        'featformsuppress',
                        'annotationform',
                        'editorlayout',
                        'excludeAttributesWMS',
                        'excludeAttributesWFS',
                        'attributeactions',
                        'aliases']:
            root_node.removeChild(root_node.firstChildElement(qmlnode))

        with open(basename + '.qml', 'w') as f:
            f.write(qmldoc.toString().encode('utf-8'))

        with open(basename + '.qlf', 'w') as f:
            f.write(doc.toString().encode('utf-8'))

        return layer_name


class ImportTranslator:
    tsfile = None

    def __init__(self):
        self._translator = QTranslator()
        self.locale = QSettings().value("locale/userLocale", QLocale.system().name())
        self.contexts = dict()

    def set_translation_file(self, basename, directory):
        '''
        Set the translation file which will be used for loading translations and writing translatable strings.
        :param basename: The basename of the file without locale suffix (e.g. 'qgep-project' when the full name is
                         qgep-project_de.qm)
        :param directory: The directory in which the translation files can be found
        '''
        self.tsfile = os.path.join(directory, basename) + '.ts'
        assert self._translator.load(basename + '_' + self.locale, directory)

    def postload_layer(self, layer):
        tsdoc = QDomDocument()
        with open(self.tsfile, 'r') as f:
            tsdoc.setContent(f.read())
        tsnode = tsdoc.firstChildElement('TS')
        tscontexts = tsnode.elementsByTagName('context')
        for context, messages in self.contexts.items():
            contextfound = False
            # Check if the context node exists
            for i in range(tscontexts.count()):
                tscontext = tscontexts.at(i)
                if tscontext.firstChildElement('name').text() == context:
                    tscontext = tscontext.toElement()
                    contextfound = True
                    break

            # If there's no context node for this context yet create one
            if not contextfound:
                tscontext = tsdoc.createElement('context')
                tsctxname = tsdoc.createElement('name')
                tsctxname.appendChild(tsdoc.createTextNode(context))
                tscontext.appendChild(tsctxname)
                tsnode.appendChild(tscontext)

            # Loop through all the messages in this context
            for message, default in messages.items():
                msgfound = False
                tsmessages = tscontext.elementsByTagName('message')

                # Loop through existing messages and see if it's already there
                for m in range(tsmessages.count()):
                    tsmessage = tsmessages.at(m)
                    if tsmessage.firstChildElement('source').text() == message:
                        msgfound = True
                        break

                # If it's not yet there, add it
                if not msgfound:
                    tsmessage = tsdoc.createElement('message')
                    tscontext.appendChild(tsmessage)
                    tsmsgsource = tsdoc.createElement('source')
                    tsmsgsource.appendChild(tsdoc.createTextNode(message))
                    tsmessage.appendChild(tsmsgsource)
                    tsmsgtranslation = tsdoc.createElement('translation')
                    tsmsgtranslation.appendChild(tsdoc.createTextNode(default))
                    tsmessage.appendChild(tsmsgtranslation)

        with open(self.tsfile, 'w') as f:
            f.write(tsdoc.toString().encode('utf-8'))

    def postload_definition(self, layer):
        ctx = self.layer_to_context(layer)

        # Translate non-value-list tables
        # Table name, aliases, forms...
        if self.layer_to_context(layer)[:3] != 'vl_':
            flds = layer.pendingFields()
            for fi in range(flds.count()):
                layer.addAttributeAlias(fi, self.tr('fld_' + ctx, flds.at(fi).name()))
                self.add_translation('fld_' + ctx, flds.at(fi).name())

            self.add_translation('lyr_' + ctx, ctx)
            layer.setLayerName(self.tr('lyr_' + ctx, ctx, True))

            # reference the proper column for value list widgets
            for i in range(layer.pendingFields().count()):
                if layer.editorWidgetV2(i) == 'ValueRelation':
                    cfg = layer.editorWidgetV2Config(i)
                    cfg['Value'] = 'value_{}'.format(self.locale[:2])
                    layer.setEditorWidgetV2Config(i, cfg)

            for ae in layer.attributeEditorElements():
                self.translate_dnd_form('frm_' + ctx, ae)

    def layer_to_context(self, layer):
        return QgsDataSourceURI(layer.dataProvider().dataSourceUri()).table()

    def tr(self, context, message, fallback=False):
        tra = self._translator.translate(context, message)

        # QMessageBox.information(None, "Translator", 'context: {} message: {} tra: {}'.format(context,message, tra))

        if tra:
            return tra
        elif fallback:
            return message
        else:
            return ''

    def translate_dnd_form(self, ctx, container):
        try:
            for ae in container.children():
                self.translate_dnd_form(ctx, ae)
            if container.type() == QgsAttributeEditorElement.AeTypeContainer:
                name = container.name()
                container.setName(self.tr(ctx, name, True))
                self.add_translation(ctx, name)
        except AttributeError:
            pass

    def add_translation(self, context, name, default=None):
        if not default:
            default = name

        try:
            self.contexts[context][name] = default
        except KeyError:
            self.contexts[context] = dict()
            self.contexts[context][name] = default




class LayerImporter:
    """
    Imports a layer including its dependencies and styling information from a .qlf file in a layersaver folder
    """

    def __init__(self, basepath):
        """
        Initialize the LayerImporter
        :param basepath: The path where the layer definitions can be found
        :return:
        """
        self.relations = list()
        self.basepath = basepath
        self.loaded_layers = list()
        self.importprocessors = list()

    def add_import_processor(self, p):
        self.importprocessors.append(p)

    def read_layer_tree_path(self, element, lt_node):
        """
        Creates missing subnodes in the layer tree from a layer-tree-group XML element

        :param element: The XML element to inspect
        :param lt_node: The layer tree node in which the subnode should be created
        :return: The newly created subnode
        """
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
        print 'Loading layer ' + layer

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

        datasource_node = maplayer_node.firstChildElement('datasource')
        ds = re.sub('service=\'[a-zA-Z_]+\'', 'service=\'pg_qgep\'', datasource_node.toElement().text())
        tn = doc.createTextNode(ds)
        dn = doc.createElement('datasource')
        dn.appendChild(tn)

        maplayer_node.replaceChild(datasource_node, dn)

        for i in range(deps_nodes.count()):
            print 'Loading dep'
            self.load_layer_definition(deps_nodes.at(i).toElement().text())
            print 'Loaded dependency ' + deps_nodes.at(i).toElement().text()
        print 'Continue ... ' + layer

        if maplayer_node.attribute('type') == 'vector':
            lyr = QgsVectorLayer()
        elif maplayer_node.attribute('type') == 'raster':
            lyr = QgsRasterLayer()
        else:
            raise TypeError('Cannot handle layer ' + layer + ' of type ' + maplayer_node.attribute(
                'type') + ' - ' + maplayer_node.tagName())

        res = lyr.readLayerXML(doc.documentElement())
        assert res, 'Layer could not be loaded {} ({})'.format(layer, res)
        QgsMapLayerRegistry.instance().addMapLayer(lyr, False)

        lt_node = self.read_layer_tree_path(maplayer_node.firstChildElement('layer-tree-group'),
                                            QgsProject.instance().layerTreeRoot())
        lt_node.insertLayer(-1, lyr)

        relnode = maplayer_node.firstChildElement('relations')
        relnodes = relnode.elementsByTagName('relation')
        for i in range(relnodes.count()):
            self.relations.append(relnodes.at(i))

        lyr.loadNamedStyle(basename + '.qml')

        qmldoc = QDomDocument('qml')
        qmlfile = QFile(basename + '.qml')
        qmlfile.open(QIODevice.ReadOnly)
        qmldoc.setContent(qmlfile)
        qmlfile.close()

        for p in self.importprocessors:
            p.postload_definition(lyr)
        print 'Loaded and processed ' + layer

    def load_layer(self, layer):
        assert len(self.relations) == 0
        self.load_layer_definition(layer)
        for r in self.relations:
            rel = QgsRelation.createFromXML(r)
            QgsProject.instance().relationManager().addRelation(rel)

        self.relations = list()
        for p in self.importprocessors:
            p.postload_layer(layer)
