""" Twistmc (Twist my components!) is a Twisted-based Python library.

The main purpose of Twistmc is to help programmers implement complex
component-based Twisted applications without relying on Twisted service
hierarchy. Service hierarchy is great, however somehow limited to the
extent of constraining large applications and imposing monolythic
designs.

Twistmc grants the programmer with a simple syntax for semantically
describe complex dependencies between application components and
hide all the internals of aynchronous dependency management.

Details of implementation do look like a dirty hack for a reason: they
are in fact a terrible accumulation of dirty hacks. Improvements to the
core structure of this module are more than welcome. However, as very
ofter reminded by glyph himself: the use of deferred objects for
synchronization is a misuse of Twisted primitives; let's keep misusing
those and build some interesting framework extension upon them.
"""

import inspect
import functools
import breadcrumbs  # pylint: disable=F0401
import types

from twisted.internet import defer, reactor
from zope import interface  # pylint: disable=F0401


#: Flag for installed classes.
INSTALLED = "__twistmc_installed__"

#: Name of the setup method list.
SETUP = "__twistmc_setup__"

#: Name of the component ready-flag deferred.
READY = "__twistmc_ready__"

#: List of plugin attributes.
PLUGINS = "__twistmc_plugins__"


def install(type_locals):
    """ Install component-related metadata into the class being declared.
    """
    # Replace the class metaclass with our component implementation, so
    # that the class is automagically populated with necessary attributes
    # for dependency management. Make sure that the old metaclass is saved
    # and called properly: we rely heavily on Twisted, which itself makes
    # extensive use of metaclass hacks.
    if INSTALLED not in type_locals:
        if "__metaclass__" in type_locals:
            chain = type_locals["__metaclass__"]
        else:
            chain = type
        type_locals["__metaclass__"] = functools.partial(metaclass, chain)
        type_locals[INSTALLED] = True
    # Set some default values for setup and teardon methods.
    if not SETUP in type_locals:
        type_locals[SETUP] = list()


def plugin(function, *args, **kwargs):
    """ Plug an object inside a component.

    Components do not have to be declared as such. Any class that
    declares a class attribute by using plugin() is implicitly
    declared as a Twistmc component. Thus, components may inherit from
    usual Twisted classes or anything else.

    Plugins may be either callable objects that will be called at instanciation
    time in order to create simple objects and other components to depend on,
    or they may be interface (zope interfaces). Components that depend on
    interfaces are not started up until another complement is ready, that
    implements the interface.
    """
    # Install if not yet.
    install(inspect.currentframe(1).f_locals)
    # Plugins are actually instanciated whenever the class itself is
    # instanciated.
    return Plugin(function, *args, **kwargs)


def collection(iface):
    """ Plug a collection inside a component.

    Simply create a collection plugin as a class attribute (implementing the
    descriptor protocol).
    """
    return Collection(iface)


def setup(function):
    """ Method decorator to declare a setup method.

    The setup methods are called once every dependency is cleared. They
    may return a Twisted deferred object in order for components
    depending on this one to wait until setup is actually complete.

    ..warning::

        There is no guarantee that setup methods are invoked in the
        same order as declared. They may even run all a once if built
        on the asynchronous paradigm.
    """
    # Install if not yet.
    install(inspect.currentframe(1).f_locals)
    # Append a setup method
    inspect.currentframe(1).f_locals[SETUP].append(function)


def teardown(function):
    """ Method decorator to declare a teardown method.

    The teardown method is called to tear the module down, ie. put the
    component in a passive state where it shall not modify the application
    state anymore (there is no simple way to cold-remove a component).

    ..notice::

        The teardown method must be called manually, il will not be called
        by TwistMC when components are garbage collected. Calling it manually
        will however remove the component from internal TwistMC registries.
    """
    def replacement(obj, *args, **kwargs):
        """ Replacement function that performs teardown.
        """
        run_teardown(obj)
        return function(obj, *args, **kwargs)

    return replacement


def ready(obj):
    """ Return a deferred fired when the object is ready.
    """
    deferred = defer.Deferred()
    if hasattr(obj, READY):
        getattr(obj, READY).addCallbacks(deferred.callback, deferred.errback)
    else:
        deferred.callback(None)
    return deferred


def metaclass(chain, classname, parents, attributes):
    """ Metaclass for every implicit component.
    """
    # Chain the class creation.
    objtype = chain(classname, parents, attributes)
    # We ultimately simply aim at re-defining the initialization
    # method for easy instance interception.
    objtype.__new__ = functools.partial(
        new_component, objtype.__new__, objtype.__init__)
    # Add the objtype plugins: include both local and parent
    # plugins. Also rebuild the setup functions list.
    plugins = list()
    setups = list()
    for parent in parents:
        if hasattr(parent, PLUGINS):
            plugins.extend(getattr(parent, PLUGINS))
        if hasattr(parent, SETUP):
            setups.extend(getattr(parent, SETUP))
    for attribute in attributes.itervalues():
        if type(attribute) is Plugin:
            plugins.append(attribute)
    setattr(objtype, PLUGINS, plugins)
    setattr(objtype, SETUP, setups + getattr(objtype, SETUP))
    # Return the freshly built object type.
    return objtype


def new_component(new, init, objtype, *args, **kwargs):
    """ Replace the init method after creating the object.

    :param new: The original instance creation function.
    :param init: The original initialization function.
    :param objtype: The object type to create an instance of.
    """
    obj = new(objtype, *args, **kwargs)
    objtype.__init__ = types.MethodType(functools.partial(
        init_component, init, objtype), obj)
    return obj


def init_component(init, objtype, obj, *args, **kwargs):
    """ Replacement method for the initialization of components.

    :param init: The original initialization function.
    :param objtype: The object type to create an instance of.
    :param obj: The object to be initialized.
    """
    # First call the original init method.
    init(obj, *args, **kwargs)
    # Simply set the instance-specific deferred object to synchronize with
    # dependant components.
    setattr(obj, READY, defer.Deferred())
    # Do not load plugins immediately, so that the caller can perform external
    # attribute initialization before breadcrumbs are resolved.
    reactor.callLater(0.0, init_plugins, objtype, obj)  # pylint: disable=E1101


def init_plugins(objtype, obj):
    """ Init the component plugins.

    :param objtype: The object type to create an instance of.
    :param obj: The object to be initialized.
    """
    # List every component to wait for before starting this very one.
    awaiting = list()
    # Initialize plugins.
    if hasattr(objtype, PLUGINS):
        for plugin_attr in getattr(objtype, PLUGINS):
            awaiting.append(plugin_attr.init(obj))
    # Explicitely wait for every dependance to be ready, then start this one
    # and finally set it as ready.
    deferred = defer.DeferredList(awaiting)
    deferred.addCallback(run_setup, obj, objtype)
    deferred.addCallback(set_ready, obj)


def run_setup(_, obj, objtype):
    """ Run every setup function on the given instance

    The first parameter is the result of a deferred and is thus ignored.

    :param obj: Instance to run the setup for.
    :param objtype: Object type of the instance.
    :rtype: A deferred object to wait for.
    """
    # List every defer the wait for them.
    defers = list()
    for setup_method in getattr(objtype, SETUP):
        # Use maybeDeferred so that setup functions may be straightforward
        # and not bother returning deferred objects.
        defers.append(defer.maybeDeferred(setup_method, obj))
    return defer.DeferredList(defers)


def run_teardown(obj):
    """ One of the component instance teardown functions was run.
    """
    # Mostly make sure that no component is teared down that was used as a
    # plugin in another component.
    for plugin_obj in Plugin.plugins:
        if obj in plugin_obj.values.itervalues():
            raise RuntimeError("Other components depend on this one")
    # Also remove the component from the registry so that it is not provided
    # to anyone anymore.
    for iface in interface.providedBy(obj):
        if iface in Plugin.registry and obj in Plugin.registry[iface]:
            Plugin.registry[iface].remove(obj)


def set_ready(_, obj):
    """ Set the given instance as ready

    The first parameter is the result of a deferred and is thus ignored.

    :param obj: The instance to set as ready.
    """
    # For every interface imlemented by the ready object, take actions
    # and populate the registry.
    # Make sure we iterate on a copy of the dictionary keys because the
    # dictionary will be altered during the loop.
    if interface.providedBy(obj):
        for iface in interface.providedBy(obj):
            if iface in Plugin.awaiting:
                for deferred in Plugin.awaiting[iface]:
                    deferred.callback(obj)
                del Plugin.awaiting[iface]
            if iface not in Plugin.registry:
                Plugin.registry[iface] = list()
            Plugin.registry[iface].append(obj)
    getattr(obj, READY).callback(None)


class Plugin(object):
    """ Implementation of the property protocol for plugin attributes.
    """

    #: Plugin registry.
    plugins = list()

    #: Registry of components per interface implemented.
    registry = dict()

    #: Registry of plugins awaiting for specific interfaces to be implemented.
    awaiting = dict()

    def __init__(self, function, *args, **kwargs):
        self.constructor = (function, args, kwargs)
        self.values = dict()
        self.plugins.append(self)

    def init(self, obj):
        """ Instanciate the plugin for a given component instance.

        :param obj: The obj to instanciate this plugin for.
        :rtype: A deferred object that fires when the plugin is ready.
        """
        # Calling the init method twice for the same object does not make
        # much sens. Maybe the exception could be avoided and a fallback
        # behavior implemented. However, one should never call init manually.
        if obj in self.values:
            raise ValueError("Cannot initialize a TwistMC plugin twice.")
        dependency, args, kwargs = self.constructor
        # Check if the dependency is an interface. In that case, check if the
        # interface is implemented already. If not, defer until an
        # implementation is available.
        if isinstance(dependency, interface.interface.InterfaceClass):
            if dependency in Plugin.registry:
                self.values[obj] = Plugin.registry[dependency][0]
                return defer.succeed(None)
            else:
                if dependency not in Plugin.awaiting:
                    Plugin.awaiting[dependency] = list()
                deferred = defer.Deferred()
                deferred.addCallback(self.assign, obj)
                Plugin.awaiting[dependency].append(deferred)
                return deferred
        else:
            # Collapse any breadcrumbs.
            args = list(args)
            for index, value in enumerate(args):
                if type(value) is breadcrumbs.Breadcrumb:
                    args[index] = breadcrumbs.collapse(value, obj)
            for index, value in kwargs.iteritems():
                if type(value) is breadcrumbs.Breadcrumb:
                    args[index] = breadcrumbs.collapse(value, obj)
            # Call the object constructor. This might be an actual type
            # for type instance construction or any callable object (function,
            # etc.).
            self.values[obj] = dependency(*args, **kwargs)  # pylint: disable=W0142,C0301
            if hasattr(self.values[obj], READY):
                return getattr(self.values[obj], READY)
            else:
                return defer.succeed(None)

    def assign(self, implementation, obj):
        """ Assign an implementation to an object.
        """
        self.values[obj] = implementation

    def __get__(self, obj, objtype=None):
        if obj in self.values:
            return self.values[obj]
        else:
            raise ValueError("Attribute accessed before ready")

    def __set__(self, _obj, _value):
        raise TypeError("Plugins can not be modified")

    def __deleted__(self, _obj):  # pylint: disable=R0201
        raise TypeError("Plugins can not be deleted")


class Collection(object):  # pylint: disable=R0903
    """ Very specific plugin that simply provides a list of available
    components that implement the given interface.
    """

    def __init__(self, iface):
        self.iface = iface

    def __get__(self, obj, objtype=None):
        try:
            return Plugin.registry[self.iface][:]
        except KeyError:
            return list()

    def __set__(self, _obj, _value):
        raise TypeError("Plugins can not be modified")

    def __deleted__(self, _obj):  # pylint: disable=R0201
        raise TypeError("Plugins can not be deleted")
