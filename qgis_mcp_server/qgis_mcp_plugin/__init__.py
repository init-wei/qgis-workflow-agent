def classFactory(iface):
    from .qgis_mcp_plugin import classFactory as create_plugin

    return create_plugin(iface)
