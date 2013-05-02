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

from twisted.internet import defer, reactor
from zope import interface


#: Flag for installed classes.
INSTALLED = "__twistmc_installed__"

#: Name of the setup method list.
SETUP = "__twistmc_setup__"

#: Name of the component ready-flag deferred.
READY = "__twistmc_ready__"


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


def component(objtype):
    """ Class decorator for explicit component declaration.
    """
    # We ultimately simply aim at re-defining the initialization
    # method for easy instance interception.
    objtype.__new__ = functools.partial(new_component, objtype.__new__, objtype)
    return objtype


def metaclass(chain, classname, parents, attributes):
    """ Metaclass for every implicit component.
    """
    # Simply call the class decorator.
    return component(chain(classname, parents, attributes))


def new_component(new, objtype, *args, **kwargs):
    """ Replacement method for the initialization of components.

    :param function new: The original __new__ function for the given objtype.
    :param objtype: The object type to create an instance of.
    """
    # First create the new instance in a very classical way.
    obj = new(*args, **kwargs)
    # Simply set the instance-specific deferred object to synchronize with
    # dependant components.
    setattr(obj, READY, defer.Deferred())
    # List every component to wait for before starting this very one.
    # Adding a fooldguard deferred object adds some overhead and may sound
    # useless. However, Twisted internel optimizations have deferred objects
    # fire right when adding callbacks if the deferred has already been fired.
    # The foolguard help ensuring nothing will fire until back to the reactor
    # loop.
    foolguard = defer.Deferred()
    reactor.callLater(0.0, foolguard.callback, None)
    awaiting = [foolguard]
    # Simply add every attribute if its type is Plugin. Also make sure that
    # plugins are properly initialized for the given instance.
    for key, value in objtype.__dict__.iteritems():
        if type(value) is Plugin:
            awaiting.append(value.init(obj))
    # Explicitely wait for every dependance to be ready, then start this one
    # and finally set it as ready.
    deferred = defer.DeferredList(awaiting)
    deferred.addCallback(run_setup, obj, objtype)
    deferred.addCallback(set_ready, obj)
    # Return the fresh instance.
    return obj


def run_setup(_, obj, objtype):
    """ Run every setup function on the given instance

    The first parameter is the result of a deferred and is thus ignored.

    :param obj: Instance to run the setup for.
    :param objtype: Object type of the instance.
    :rtype: A deferred object to wait for.
    """
    # List every defer the wait for them.
    defers = list()
    for setup in getattr(objtype, SETUP):
        # Use maybeDeferred so that setup functions may be straightforward
        # and not bother returning deferred objects.
        defers.append(defer.maybeDeferred(setup, obj))
    return defer.DeferredList(defers)


def set_ready(_, obj):
    """ Set the given instance as ready

    The first parameter is the result of a deferred and is thus ignored.

    :param obj: The instance to set as ready.
    """
    # Make sure we iterate on a copy of the dictionary keys because the
    # dictionary will be altered during the loop.
    for iface in Plugin.awaiting.keys():
        if iface.providedBy(obj):
            for deferred in Plugin.awaiting[iface]:
                deferred.callback(obj)
            del Plugin.awaiting[iface]
    getattr(obj, READY).callback(None)


class Plugin(object):
    """ Implementation of the property protocol for plugin attributes.
    """

    #: Registry of components per interface implemented.
    registry = dict()

    #: Registry of plugins awaiting for specific interfaces to be implemented.
    awaiting = dict()

    def __init__(self, function, *args, **kwargs):
        self.constructor = (function, args, kwargs)
        self.values = dict()

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
            # First call the object constructor. This might be an actual type
            # for type instance construction or any callable object (function,
            # etc.).
            self.values[obj] = dependency(*args, **kwargs)
            if hasattr(self.values[obj], READY):
                return getattr(self.values[obj], READY)
            else:
                return defer.suceed(None)

    def assign(self, implementation, obj):
        """ Assign an implementation to an object.
        """
        self.values[obj] = implementation

    def __get__(self, obj, objtype=None):
        if obj in self.values:
            return self.values[obj]
        else:
            raise ValueError("Attribute accessed before ready")

    def __set__(self, obj, value):
        raise TypeError("Plugins can not be modified")

    def __deleted__(self, obj):
        raise TypeError("Plugins can not be deleted")
